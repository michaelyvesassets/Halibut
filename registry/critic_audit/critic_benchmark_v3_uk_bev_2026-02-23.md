# Critic benchmark v3: human audit package (uk_cars_first_registrations, evidence as of 2026-02-23)

Benchmark `critic_benchmark.v3`; key sha256 `7e94d61d2b84c383…`; payload sha256 `99c3eda0ab0f5685…`. **Audit status: PROPOSED.**

**For the auditor only; never shown to the critic.** The critic receives the blinded sets, without this key. Audit each item blind to any critic output. Scores are labelled AUDITED only after every item is confirmed.

## Construction

- canonical clean items first, one per brand: a continuation claim about segment share only, with correct numbers, no causal claim, and the same direction in the mechanism and the test;
- each defective item is ONE isolated mutation of a canonical item;
- a deterministic checker (`check_item`) must find no issue in a canonical item and exactly the intended issue in each mutation;
- within a set, at most one item per brand, so no variant can be compared with its own canonical.

## Severity rubric

- **high**: the claim is contradicted by the evidence, mathematically impossible, rests on a wrong central number, or its test cannot measure what it claims
- **medium**: the claim relies on facts or causal inferences the evidence does not establish, or confuses two quantities in a way that does not by itself contradict the evidence
- **low**: a weakness worth noting that does not undermine the claim (e.g. limited power, a plausible alternative)

## Audit checklist (every item)

- [ ] the item carries exactly the stated defect and no other
- [ ] every number in a clean item equals the evidence
- [ ] every mechanism refers only to quantities in the evidence (no external facts)
- [ ] the expected dimension and evidence ids are the only correct ones
- [ ] each defective item differs from its canonical by exactly one change
- [ ] absolute volume, segment share, total share and segment mix are not confused (except the planted confusion)

## Evidence used by the items

| Metric | Value (%) |
|---|---|
| `metric:non_segment_share:L4:tesla` | 0.0000 |
| `metric:non_segment_share:P4:tesla` | 0.0000 |
| `metric:segment_mix:L4:tesla` | 100.0000 |
| `metric:segment_mix:L4:volkswagen` | 19.1030 |
| `metric:segment_mix:P4:tesla` | 100.0000 |
| `metric:segment_share:L4:byd` | 4.7522 |
| `metric:segment_share:L4:tesla` | 10.6635 |
| `metric:segment_share:L4:volkswagen` | 7.7048 |
| `metric:segment_share:P4:byd` | 1.7122 |
| `metric:segment_share:P4:tesla` | 13.3182 |
| `metric:segment_share:P4:volkswagen` | 5.5099 |

## Set S1

### S1:H1: CLEAN CONTROL

- **Statement:** tesla's segment share (its share of the segment's registrations) moved from 13.32% to 10.66%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, decrease)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:tesla`, `metric:segment_share:P4:tesla`
- **Implications:** segment_share tesla decrease_vs_L4
- **Deterministic check:** no issue
- **Expected:** no factual or numerical claim, and nothing at high severity. Methodological comments (for example that continuation is a weak test) are allowed and not penalised.
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S1:H2: PLANTED DEFECT: unsupported_detail

- **Statement:** volkswagen's segment share (its share of the segment's registrations) moved from 5.51% to 7.70%. This hypothesis expects the same direction over the next two quarters; the movement is driven by recent model launches.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:volkswagen`, `metric:segment_share:P4:volkswagen`
- **Implications:** segment_share volkswagen increase_vs_L4
- **Deterministic check:** ['causal_claim', 'external_fact']
- **Accepted classifications:** unsupported_detail or causal_identification; minimum severity **medium** (ideal medium)
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S1:H3: PLANTED DEFECT: wrong_number

- **Statement:** byd's segment share (its share of the segment's registrations) moved from 1.71% to 7.13%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:byd`, `metric:segment_share:P4:byd`
- **Implications:** segment_share byd increase_vs_L4
- **Deterministic check:** ['number_mismatch']
- **Accepted classifications:** numerical_accuracy or factual_contradiction; minimum severity **medium** (ideal high); accepted evidence: `metric:segment_share:L4:byd`
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

## Set S2

### S2:H1: PLANTED DEFECT: contradicted_assumption

- **Statement:** tesla's segment share (its share of the segment's registrations) moved from 13.32% to 10.66%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, decrease)
- **Assumptions:** the recent direction persists for two quarters; tesla also sells substantial volumes outside the segment
- **Supporting evidence:** `metric:segment_share:L4:tesla`, `metric:segment_share:P4:tesla`
- **Implications:** segment_share tesla decrease_vs_L4
- **Deterministic check:** ['contradicted_assumption']
- **Accepted classifications:** factual_contradiction; minimum severity **medium** (ideal high); accepted evidence: `metric:non_segment_share:L4:tesla`, `metric:non_segment_share:P4:tesla`, `metric:segment_mix:L4:tesla`, `metric:segment_mix:P4:tesla`
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S2:H2: PLANTED DEFECT: quantity_confusion

- **Statement:** volkswagen's segment share (its share of the segment's registrations) moved from 5.51% to 19.10%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:volkswagen`, `metric:segment_share:P4:volkswagen`
- **Implications:** segment_share volkswagen increase_vs_L4
- **Deterministic check:** ['quantity_confusion']
- **Accepted classifications:** numerical_accuracy or factual_contradiction; minimum severity **medium** (ideal high); accepted evidence: `metric:segment_mix:L4:volkswagen`, `metric:segment_share:L4:volkswagen`
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S2:H3: CLEAN CONTROL

- **Statement:** byd's segment share (its share of the segment's registrations) moved from 1.71% to 4.75%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:byd`, `metric:segment_share:P4:byd`
- **Implications:** segment_share byd increase_vs_L4
- **Deterministic check:** no issue
- **Expected:** no factual or numerical claim, and nothing at high severity. Methodological comments (for example that continuation is a weak test) are allowed and not penalised.
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

## Set S3

### S3:H1: CLEAN CONTROL

- **Statement:** volkswagen's segment share (its share of the segment's registrations) moved from 5.51% to 7.70%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:volkswagen`, `metric:segment_share:P4:volkswagen`
- **Implications:** segment_share volkswagen increase_vs_L4
- **Deterministic check:** no issue
- **Expected:** no factual or numerical claim, and nothing at high severity. Methodological comments (for example that continuation is a weak test) are allowed and not penalised.
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S3:H2: PLANTED DEFECT: misencoded_test

- **Statement:** byd's segment share (its share of the segment's registrations) moved from 1.71% to 4.75%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:byd`, `metric:segment_share:P4:byd`
- **Implications:** segment_share_gap byd-volkswagen above_reference vs `metric:segment_share:L4:volkswagen`
- **Deterministic check:** ['misencoded_test']
- **Accepted classifications:** test_validity; minimum severity **medium** (ideal high)
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S3:H3: PLANTED DEFECT: impossible_prediction

- **Statement:** tesla's segment share (its share of the segment's registrations) moved from 13.32% to 10.66%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, decrease)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:tesla`, `metric:segment_share:P4:tesla`
- **Implications:** segment_share tesla decrease_vs_L4; segment_mix tesla increase_vs_L4
- **Deterministic check:** ['impossible_prediction']
- **Accepted classifications:** test_validity or factual_contradiction or numerical_accuracy; minimum severity **medium** (ideal high); accepted evidence: `metric:segment_mix:L4:tesla`
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

## Set S4

### S4:H1: CLEAN CONTROL

- **Statement:** tesla's segment share (its share of the segment's registrations) moved from 13.32% to 10.66%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, decrease)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:tesla`, `metric:segment_share:P4:tesla`
- **Implications:** segment_share tesla decrease_vs_L4
- **Deterministic check:** no issue
- **Expected:** no factual or numerical claim, and nothing at high severity. Methodological comments (for example that continuation is a weak test) are allowed and not penalised.
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S4:H2: CLEAN CONTROL

- **Statement:** byd's segment share (its share of the segment's registrations) moved from 1.71% to 4.75%. This hypothesis expects the same direction over the next two quarters; it makes no claim about why.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:byd`, `metric:segment_share:P4:byd`
- **Implications:** segment_share byd increase_vs_L4
- **Deterministic check:** no issue
- **Expected:** no factual or numerical claim, and nothing at high severity. Methodological comments (for example that continuation is a weak test) are allowed and not penalised.
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

### S4:H3: PLANTED DEFECT: causal_overclaim

- **Statement:** volkswagen's segment share (its share of the segment's registrations) moved from 5.51% to 7.70%. This hypothesis expects the same direction over the next two quarters; a continuation would demonstrate that the brand's appeal to segment buyers is the cause.
- **Mechanism:** short-run persistence of the recent within-segment movement → a brand's within-segment share tends to keep its recent direction for a short time (segment_share, increase)
- **Assumptions:** the recent direction persists for two quarters
- **Supporting evidence:** `metric:segment_share:L4:volkswagen`, `metric:segment_share:P4:volkswagen`
- **Implications:** segment_share volkswagen increase_vs_L4
- **Deterministic check:** ['causal_claim']
- **Accepted classifications:** causal_identification or test_validity or unsupported_detail; minimum severity **medium** (ideal medium)
- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )

## Sign-off

- Auditor: ____  Date: ____  All items confirmed: [ ] yes
- After sign-off only: set `audit.status` to `audited` (and the auditor) in the JSON.
