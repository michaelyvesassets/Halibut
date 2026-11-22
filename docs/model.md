# Halibut simulation model, v0.1

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
2. **Preference drift** (`[preference_drift]`): after each week,
   `π_ij ← π_ij + clip(rate·s_i, 0, 1)·(S_ij − π_ij)`. This is a conformity
   rule in which tastes move toward what neighbours consume. It is persistent.

Either mechanism can be disabled while keeping the other. Note that conformity
drift pulls preference for *any* minority product down, so it structurally
favours incumbents (see Limitations).

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

## Limitations

- **Uncalibrated.** All parameters are illustrative.
- **Similar alternatives.** Plain logit tends to over-predict substitution
  between similar alternatives. A nested logit is the upgrade path once more
  products are added.
- **Incumbent bias in drift.** The conformity drift rule systematically erodes
  preference for minority products, and the choice of mechanism materially
  affects results. In `coffee_vs_matcha_v1`, turning drift off raises matcha's
  final share from about 2% to 5% in the control, and from about 6% to 14% in
  the combined scenario.
- **Exogenous supply side.** There are no firm agents. Prices, positioning and
  availability move only through scheduled interventions.
- **Fixed structure.** The network never changes, and there is one purchase
  decision per week with a fixed quantity per consumer, no savings and no
  stockpiling.
- **Stock-outs.** Availability is an independent per-consumer, per-week draw,
  with no inventory and no spatial or regional structure yet.
