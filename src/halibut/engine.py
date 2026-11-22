"""Simulation engine: builds a world for a seed and steps a scenario week by week.

A ``World`` (population, network, initial adoption, base market) depends only
on the seed, never on the scenario, and is built once per seed and shared by
every scenario run for that seed. Together with per-run re-creation of the
choice and availability random streams, this gives every scenario identical
starting conditions and common random numbers.

The engine performs no I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .config import ExperimentConfig, ScenarioConfig
from .decision import DecisionContext, LogitChoiceModel
from .influence import drift_preferences
from .interventions import effective_market, effective_population, resolve_targets, seeding_events
from .market import Market
from .metrics import segment_metrics, tick_metrics
from .network import SocialNetwork, build_network
from .population import Population, generate_population, initial_products
from .rng import stream


@dataclass(frozen=True)
class World:
    seed: int
    population: Population
    market: Market
    network: SocialNetwork
    initial_current: np.ndarray


@dataclass
class ScenarioRun:
    scenario: str
    seed: int
    metrics: list[dict]
    segment_metrics: list[dict]
    runtime_seconds: float
    choices: np.ndarray | None = None  # (weeks, N) when traced
    final_preference: np.ndarray | None = None


def build_world(cfg: ExperimentConfig, seed: int) -> World:
    population = generate_population(cfg.population, cfg.product_ids, stream(seed, "population"))
    network = build_network(cfg.network, population.size, stream(seed, "network"))
    current = initial_products(population, cfg.population, cfg.product_ids, stream(seed, "initial_state"))
    return World(seed, population, Market.from_config(cfg.products), network, current)


def run_scenario(
    cfg: ExperimentConfig, world: World, scenario: ScenarioConfig, *, trace: bool = False
) -> ScenarioRun:
    started = time.perf_counter()
    pop, net = world.population, world.network
    n, j_count = pop.size, world.market.size
    interventions = scenario.interventions
    targets = resolve_targets(interventions, net)
    model = LogitChoiceModel.from_config(cfg.decision, cfg.social_influence)
    need_share = cfg.social_influence.enabled or cfg.preference_drift.enabled

    choice_rng = stream(world.seed, "choice")
    availability_rng = stream(world.seed, "availability")

    preference = pop.preference.copy()
    current = world.initial_current.copy()
    last_choice = world.initial_current.copy()  # initial adopters count as last week's buyers

    metrics, seg_rows = [], []
    choice_trace = np.empty((cfg.weeks, n), dtype=np.int8) if trace else None

    for week in range(cfg.weeks):
        for event in seeding_events(interventions, week):
            idx = targets[(event.target, event.fraction)]
            j = world.market.index(event.product)
            current[idx] = j + 1
            last_choice[idx] = j + 1
            if event.preference is not None:
                preference[idx, j] = event.preference

        market = effective_market(world.market, interventions, week)
        attrs = effective_population(pop, interventions, week, targets)

        # Random draws happen every week in a fixed order regardless of scenario.
        availability_u = availability_rng.random((n, j_count))
        choice_u = choice_rng.random(n)

        share = net.neighbour_product_share(last_choice, j_count) if need_share else None
        ctx = DecisionContext(
            price=market.price,
            quality=market.quality,
            available=availability_u < market.availability[None, :],
            preference=preference,
            current=current,
            neighbour_share=share if cfg.social_influence.enabled else None,
            **{k: attrs[k] for k in (
                "budget", "consumption", "price_sensitivity", "brand_loyalty",
                "switching_resistance", "social_susceptibility",
            )},
        )
        choices = model.choose(ctx, choice_u)
        new_current = np.where(choices > 0, choices, current)

        metrics.append(
            tick_metrics(
                week, market.product_ids, market.price, market.quality, market.availability,
                choices, last_choice, current, new_current,
                attrs["consumption"], attrs["budget"], preference,
            )
        )
        seg_rows.extend(
            segment_metrics(week, market.product_ids, pop.segment, pop.segment_names,
                            choices, new_current, attrs["consumption"])
        )
        if trace:
            choice_trace[week] = choices

        if cfg.preference_drift.enabled:
            preference = drift_preferences(
                preference, attrs["social_susceptibility"], share, cfg.preference_drift.rate
            )
        current, last_choice = new_current, choices

    return ScenarioRun(
        scenario=scenario.name,
        seed=world.seed,
        metrics=metrics,
        segment_metrics=seg_rows,
        runtime_seconds=time.perf_counter() - started,
        choices=choice_trace,
        final_preference=preference if trace else None,
    )
