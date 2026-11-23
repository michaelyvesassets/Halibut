"""Simulation engine: builds a world for a seed and steps a scenario week by week.

A ``World`` (population, network, initial adoption, base market and, when
configured, the post-warm-up state) depends only on the seed, never on the
scenario. It is built once per seed and shared by every scenario run for that
seed. Together with per-run re-creation of the choice and availability random
streams, this gives every scenario identical starting conditions and common
random numbers.

Warm-up (``experiment.warmup_weeks``, default 0) runs the control dynamics for
W unrecorded weeks before week 0, from dedicated random streams, so the model
starts from a state consistent with its own dynamics. With W = 0 the engine is
bit-for-bit identical to engine 0.1.0.

The engine performs no I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .config import ExperimentConfig, ScenarioConfig
from .decision import DecisionContext, LogitChoiceModel, sample_options
from .influence import apply_drift
from .interventions import effective_market, effective_population, resolve_targets, seeding_events
from .market import Market
from .metrics import segment_metrics, tick_metrics
from .network import SocialNetwork, build_network
from .population import Population, generate_population, initial_products
from .rng import stream

_CONTEXT_ATTRS = (
    "budget", "consumption", "price_sensitivity", "brand_loyalty",
    "switching_resistance", "social_susceptibility",
)


@dataclass(frozen=True)
class World:
    seed: int
    population: Population
    market: Market
    network: SocialNetwork
    initial_current: np.ndarray  # state at week 0 (after warm-up, if any)
    initial_last_choice: np.ndarray
    initial_preference: np.ndarray
    warmup_weeks: int = 0


@dataclass
class ScenarioRun:
    scenario: str
    seed: int
    metrics: list[dict]
    segment_metrics: list[dict]
    runtime_seconds: float
    choices: np.ndarray | None = None  # (weeks, N) when traced
    final_preference: np.ndarray | None = None


@dataclass
class _State:
    preference: np.ndarray
    current: np.ndarray
    last_choice: np.ndarray


@dataclass
class _Step:
    choices: np.ndarray
    new_current: np.ndarray
    available: np.ndarray
    affordable: np.ndarray
    share: np.ndarray | None
    probabilities: np.ndarray | None = None
    in_market: np.ndarray | None = None


def _step(cfg, model, pop, net, state, market, attrs, availability_u, choice_u, full_availability=False,
          in_market=None) -> _Step:
    """Decide one tick. Does not update ``state`` (see ``_advance``).

    ``in_market`` (bool array) is given only when the purchase-opportunity
    model is enabled; out-of-market consumers make no choice (option 0) and
    keep their current product.
    """
    j_count = market.size
    need_share = cfg.social_influence.enabled or cfg.preference_drift.effective_model != "none"
    signal = state.current if cfg.social_influence.signal == "current_product" else state.last_choice
    share = net.neighbour_product_share(signal, j_count) if need_share else None
    if full_availability:
        available = np.ones_like(availability_u, dtype=bool)
    else:
        available = availability_u < market.availability[None, :]
    ctx = DecisionContext(
        price=market.price,
        quality=market.quality,
        available=available,
        preference=state.preference,
        current=state.current,
        neighbour_share=share if cfg.social_influence.enabled else None,
        **{k: attrs[k] for k in _CONTEXT_ATTRS},
    )
    probabilities = model.probabilities(ctx)
    choices = sample_options(probabilities, choice_u)  # identical to model.choose(ctx, choice_u)
    if in_market is not None:
        choices = np.where(in_market, choices, 0)
    affordable = ctx.cost() <= ctx.budget[:, None]
    return _Step(choices, np.where(choices > 0, choices, state.current), available, affordable, share,
                 probabilities, in_market)


def _advance(cfg, state: _State, step: _Step, susceptibility, base_preference) -> None:
    state.preference = apply_drift(
        cfg.preference_drift, state.preference, base_preference, susceptibility, step.share
    )
    state.current, state.last_choice = step.new_current, step.choices


def build_world(cfg: ExperimentConfig, seed: int) -> World:
    population = generate_population(cfg.population, cfg.product_ids, stream(seed, "population"))
    network = build_network(cfg.network, population.size, stream(seed, "network"))
    current = initial_products(population, cfg.population, cfg.product_ids, stream(seed, "initial_state"))
    market = Market.from_config(cfg.products)
    state = _State(population.preference, current, current)
    if cfg.warmup_weeks:
        state = _State(population.preference.copy(), current.copy(), current.copy())
        model = LogitChoiceModel.from_config(cfg.decision, cfg.social_influence)
        attrs = {a: getattr(population, a) for a in _CONTEXT_ATTRS}
        choice_rng = stream(seed, "warmup_choice")
        availability_rng = stream(seed, "warmup_availability")
        n, j = population.size, market.size
        opportunity_rng = stream(seed, "warmup_opportunity") if cfg.purchase_opportunity.enabled else None
        for _ in range(cfg.warmup_weeks):
            availability_u = availability_rng.random((n, j))
            choice_u = choice_rng.random(n)
            in_market = (opportunity_rng.random(n) < cfg.purchase_opportunity.probability
                         if opportunity_rng is not None else None)
            step = _step(cfg, model, population, network, state, market, attrs, availability_u, choice_u,
                         in_market=in_market)
            _advance(cfg, state, step, attrs["social_susceptibility"], population.preference)
    return World(seed, population, market, network, state.current, state.last_choice,
                 state.preference, cfg.warmup_weeks)


def run_scenario(
    cfg: ExperimentConfig,
    world: World,
    scenario: ScenarioConfig,
    *,
    trace: bool = False,
    collect_segments: bool = True,
    full_availability: bool = False,
) -> ScenarioRun:
    """Run one scenario on a world.

    ``full_availability`` is a diagnostic counterfactual: every product is in
    stock for everyone (the availability random draws are still consumed, so
    common random numbers are preserved).
    """
    started = time.perf_counter()
    pop, net = world.population, world.network
    n, j_count = pop.size, world.market.size
    interventions = scenario.interventions
    targets = resolve_targets(interventions, net)
    model = LogitChoiceModel.from_config(cfg.decision, cfg.social_influence)

    choice_rng = stream(world.seed, "choice")
    availability_rng = stream(world.seed, "availability")
    opportunity_rng = stream(world.seed, "opportunity") if cfg.purchase_opportunity.enabled else None
    state = _State(world.initial_preference.copy(), world.initial_current.copy(),
                   world.initial_last_choice.copy())

    metrics, seg_rows = [], []
    choice_trace = np.empty((cfg.weeks, n), dtype=np.int8) if trace else None

    for week in range(cfg.weeks):
        for event in seeding_events(interventions, week):
            idx = targets[(event.target, event.fraction)]
            j = world.market.index(event.product)
            state.current[idx] = j + 1
            state.last_choice[idx] = j + 1
            if event.preference is not None:
                state.preference[idx, j] = event.preference

        market = effective_market(world.market, tuple(cfg.drivers) + tuple(interventions), week)
        attrs = effective_population(pop, interventions, week, targets)

        # Random draws happen every week in a fixed order regardless of scenario.
        availability_u = availability_rng.random((n, j_count))
        choice_u = choice_rng.random(n)
        in_market = (opportunity_rng.random(n) < cfg.purchase_opportunity.probability
                     if opportunity_rng is not None else None)
        step = _step(cfg, model, pop, net, state, market, attrs, availability_u, choice_u,
                     full_availability, in_market)

        metrics.append(
            tick_metrics(
                week, market.product_ids, market.price, market.quality, market.availability,
                step.choices, state.last_choice, state.current, step.new_current,
                attrs["consumption"], attrs["budget"], state.preference,
                available=step.available, affordable=step.affordable,
                probabilities=step.probabilities, in_market=step.in_market,
            )
        )
        if collect_segments:
            seg_rows.extend(
                segment_metrics(week, market.product_ids, pop.segment, pop.segment_names,
                                step.choices, step.new_current, attrs["consumption"])
            )
        if trace:
            choice_trace[week] = step.choices
        _advance(cfg, state, step, attrs["social_susceptibility"], pop.preference)

    return ScenarioRun(
        scenario=scenario.name,
        seed=world.seed,
        metrics=metrics,
        segment_metrics=seg_rows,
        runtime_seconds=time.perf_counter() - started,
        choices=choice_trace,
        final_preference=state.preference if trace else None,
    )
