# Halibut simulation model (engine 0.2)

> **Status:** synthetic. The model and every parameter in the example
> configurations are illustrative assumptions. Nothing here has been calibrated
> or validated against empirical data. Outputs describe how the model behaves,
> not how real markets will behave.

## Time, agents, options

- One tick is one week. Weeks are indexed `0 … weeks-1`.
- There are `N` consumer agents and `J` products. Each week, each consumer
  makes one category decision over `J + 1` options. Option `0` is
  **no purchase**, and options `1 … J` are the products.
- Each consumer has a **current product**: their most recent purchase, kept
  through weeks when they buy nothing. A consumer who has never bought has
  current product `0`.

## Consumer attributes

All attributes are drawn per segment from configurable distributions
(constant, uniform, normal, lognormal or beta, with optional clipping).

| Attribute | Symbol | Meaning |
|---|---|---|
| `budget` | `B_i` | weekly category spending capacity |
| `consumption` | `q_i` | servings bought in a purchase week |
| `price_sensitivity` | `λ_i` | disutility per unit of budget share spent |
| `brand_loyalty` | `L_i` | utility bonus for the current product |
| `switching_resistance` | `R_i` | utility cost of buying a product other than the current one |
| `social_susceptibility` | `s_i` | scales peer influence and preference drift |
| `preferences` | `π_ij ∈ [0,1]` | taste fit for product `j` (state that drift can change) |

Loyalty and switching resistance are deliberately different. Loyalty raises
the current product relative to *every* alternative, including not buying, so
it keeps consumers in the category. Switching resistance penalises only other
products, so it deters brand switching without deterring exit.

## Choice model: multinomial logit

Systematic utility of product `j` for consumer `i`:

```
V_ij = w_pref·π_ij + w_qual·Q_j − λ_i·(p_j·q_i / B_i)
       + L_i·[j = cur_i] − R_i·[cur_i ≠ 0 and j ≠ cur_i]
       + w_soc·s_i·S_ij                         (peer influence, optional)
V_i0 = outside_utility                          (no purchase)
```

**Feasibility.** Product `j` is feasible for `i` in a week only if it is in
stock for them (a Bernoulli draw with probability `availability_j`) **and**
`p_j·q_i ≤ B_i`. The no-purchase option is always feasible. Infeasible options
get probability exactly zero.

**Probabilities.** Random utility is `U = V + ε`, with `ε` i.i.d. Gumbel of
scale `T` (the `temperature`). Integrating out `ε` gives:

```
P_io = exp(V_io / T) / Σ_{o' feasible} exp(V_io' / T)
```

One option is sampled from `P` using a **single uniform draw per consumer**
(inverse CDF). The Gumbel noise appears *only* through the softmax and is never
also added to `V`, so randomness is not counted twice. `T = 0` is the
deterministic limit: the consumer picks the highest feasible `V`, with ties
going to the lowest option index.

## Social network

The network is a Watts–Strogatz small world. It starts as a ring lattice in
which each node links to `k/2` neighbours on each side. Each edge is then
rewired with probability `rewire_prob`, avoiding self-loops and duplicate
edges. The edge count `N·k/2` is preserved exactly. The network does not change
during a run.

`S_ij` is the fraction of `i`'s neighbours whose **last-week choice** was `j`.
Neighbours who did not buy count in the denominator.

## Social mechanisms (independently switchable)

1. **Peer influence** (`[social_influence]`): adds the `w_soc·s_i·S_ij` term to
   utility. It is short-term and changes no state.
2. **Preference drift** (`[preference_drift]`, persistent). It is applied
   after each week. Let `a_i = rate·s_i` (clipped so each update is a convex
   combination and preferences stay in [0, 1]), and let `π⁰` be the generated
   preference. The rule is selected with `model` (schema 1.1):

| `model` | Update | Fixed point | Character |
|---|---|---|---|
| `none` | no change | `π⁰` | static tastes |
| `conformity` (v1 default) | `π += a(S − π)` | `S` | tastes copy neighbours' consumption; a minority product's preference is pulled down toward its low share, so this rule **structurally favours incumbents** |
| `baseline_reversion` | `π += a(S − π) + κ(π⁰ − π)` | `(aS + κπ⁰)/(a + κ)` | conformity anchored to original taste (`reversion_rate` = κ) |
| `exposure_reinforcement` | `π += aS(1 − π) − δ(π − π⁰)` | `(aS + δπ⁰)/(aS + δ)` | exposure only raises preference (mere-exposure), and it decays back to `π⁰` (`decay_rate` = δ); never pushed below baseline |

   The step `a` is clipped to `[0, 1]` for conformity, to `[0, 1 − κ]` for
   baseline reversion, and to `[0, 1 − δ]` for exposure reinforcement. With
   these clips every update is a convex combination of values in [0, 1].
   Tests verify the fixed points and the bounds.

   `enabled = false` is equivalent to `model = "none"`. A schema 1.0 config
   always uses `conformity`.

Either mechanism can be disabled while keeping the other.

## Interventions

Each intervention is a pure function of the week, applied on top of immutable
base values:

- `product_change`: `price`, `quality` (positioning) or `availability`. The
  operation is `set`, `multiply` or `add`. It runs from `start_week` until an
  optional `end_week` (exclusive), with an optional linear phase-in over
  `ramp_weeks`.
- `population_change`: the same operations, applied to a consumer attribute for
  `all` consumers or for the `top_degree` fraction.
- `seed_adopters`: a one-off event in a given week. The targeted consumers get
  the product as their current product and as last week's purchase, and
  optionally a set preference value.

## Reproducibility and common random numbers

- Randomness comes from named streams derived from one seed with
  `numpy.random.SeedSequence(seed, spawn_key=(stream,))`. The streams are
  `population`, `network`, `initial_state`, `choice` and `availability`.
- A *world* (population, network, initial adoption) depends only on the seed.
  It is built once per seed and shared by every scenario.
- Each scenario run re-creates the `choice` and `availability` streams. The
  same numbers are drawn in the same order every week, whatever the scenario.
  Interventions never draw random numbers.
- As a result, a scenario is **bit-identical to the control before its first
  intervention** (this is tested), and treatment-minus-control differences are
  paired per seed. With shared uniforms, choices differ between scenarios only
  where the two choice CDFs straddle a consumer's uniform.
- Every output records the engine version, the config schema version and the
  SHA-256 hash of the fully resolved configuration.

## Metrics

Each week, the engine records:

- **Purchase share**: each product's share of units bought.
- **Buyer share**: each product's share of purchasers.
- **Penetration**: the fraction of the population buying each product.
- **Adoption share**: the fraction of the population with each product as
  their current product.
- **Category demand**: total units and revenue.
- **No-purchase rate**.
- **Switching rate**: switchers as a fraction of purchasers.
- **First purchases**.
- **Budget utilisation**.
- **Week-to-week flows**: a `(J+1)×(J+1)` matrix of choice transitions.

### Displacement vs contraction

Given product units before and after (start vs end of a scenario, or control vs
treatment for the same seed):

```
gain  = Σ max(Δ_j, 0)      loss = Σ max(−Δ_j, 0)
displaced   = min(gain, loss)          # demand that moved between products
contraction = max(loss − gain, 0)      # demand that left the category
expansion   = max(gain − loss, 0)      # demand that entered the category
```

Each quantity is computed per seed and then averaged, so the means of
`contraction` and `expansion` can both be non-zero when seeds disagree on the
sign. The 95% intervals use Student-t across seeds.

## Warm-up (engine 0.2, optional)

`experiment.warmup_weeks = W` (default 0) runs the **control** dynamics for
`W` unrecorded weeks before week 0. The warm-up uses its own random streams
(`warmup_choice`, `warmup_availability`). The resulting state (current
products, last-week choices, drifted preferences) becomes the shared starting
point for every scenario.

Properties:
- With `W = 0` the engine is bit-identical to 0.1.0. This is enforced by
  golden tests.
- The week-0+ random numbers are the same with or without warm-up.
- Drift anchors (`π⁰`) remain the generated preferences, not the warmed-up
  ones.

Why it exists: when the configured initial shares are not a state the model
can sustain, the control trends, and changes relative to week 0 then mix
intervention effects with the model relaxing from its starting point. Paired
treatment-vs-control comparisons stay valid either way, but they are measured
around a different state. Report results with and without warm-up.

Interventions are scheduled relative to week 0. They never act during warm-up.

## No-purchase causes (engine 0.2)

Each consumer who buys nothing in a week gets **exactly one** cause. The causes
are tested in the order below, so they are mutually exclusive and sum to the
number of non-buyers. "Current product" means the current product at the
start of the week.

1. `np_nothing_affordable`: no product is affordable (`p_j·q_i > B_i` for
   all `j`), regardless of stock.
2. `np_nothing_in_stock`: some product is affordable, but every affordable
   product is out of stock for this consumer.
3. `np_current_infeasible`: some product is feasible, but the consumer's
   current product is not (it is out of stock or unaffordable).
4. `np_voluntary`: some product is feasible, and so is the current product if
   the consumer has one; the consumer chose not to buy.

Cause 3 does not mean the stock-out *caused* the exit. A consumer with a
feasible current product might also have chosen not to buy. The causal share
is measured by the **stock-out counterfactual** (`halibut diagnose
--stockout-counterfactual`). It reruns every scenario with the same random
numbers but with all products always in stock, and compares the results.

Related metrics:
- `current_out_of_stock`: consumers whose current product is out of stock for
  them.
- `current_unaffordable`: consumers whose current product is unaffordable.
- `forced_substitution`: purchasers whose current product was infeasible, so
  they had to buy a different product.

## Diagnostics (engine 0.2)

Each run writes `diagnostics.json`.

**Accounting invariants.** Status is `fail` on any violation. The checks are:
- shares sum to 1;
- adoption shares plus `non_adopter_share` sum to 1;
- units and purchasers add up across products;
- revenue equals price × units;
- flows conserve the population and match this week's choices;
- the flows out of week t equal the flows into week t−1 (skipped in seeding
  weeks, which reassign last-week choices);
- the no-purchase causes partition the non-buyers;
- no consumer exceeds their budget.

**Behavioural checks:**
- `switching_churn`: gross vs net switching for each product pair.
  `churn_ratio = (a→b + b→a) / |a→b − b→a|`.
- `stockouts_and_no_purchase_causes`: stock-out exposure and the share of
  no-purchases due to each cause.
- `control_transient`: `warn` if the control's purchase share changes by more
  than 0.01 (absolute), or its category units by more than 2% (relative),
  between the first and last summary window.
- `monte_carlo_precision`: the paired-effect 95% CIs. Recommended seeds are
  `n·(ci95 / (0.1·|effect|))²`, the number needed for the CI half-width to
  shrink to 10% of the effect.

**Optional counterfactuals:** the stock-out counterfactual above, and
population-size convergence (the same metrics at several population sizes).

## Limitations

- **Uncalibrated.** All parameters are illustrative.
- **Similar alternatives.** Plain logit tends to over-predict substitution
  between similar alternatives. A nested logit is the upgrade path once more
  products are added.
- **Drift rule choice.** Conformity (the v1 default) systematically erodes
  preference for minority products, and the choice of drift model materially
  affects results. Phase 2 compares the alternatives. In `coffee_vs_matcha_v1`, turning drift off raises matcha's
  final share from about 2% to 5% in the control, and from about 6% to 14% in
  the combined scenario.
- **Exogenous supply side.** There are no firm agents. Prices, positioning and
  availability move only through scheduled interventions.
- **Fixed structure.** The network never changes, and there is one purchase
  decision per week with a fixed quantity per consumer, no savings and no
  stockpiling.
- **Stock-outs.** Availability is an independent per-consumer, per-week draw,
  with no inventory and no spatial or regional structure yet.
