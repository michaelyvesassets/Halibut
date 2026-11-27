# Critic benchmark v2: human audit package (UK BEV, evidence as of 2026-02-23)

Benchmark `critic_benchmark.v2`, key sha256 `c32e45532949af9b…`, payload sha256 `4ebd619493b7e358…`. **Audit status: PROPOSED.**

For the auditor only: **this package must never be shown to the critic** (the critic receives the blinded sets without the key). Audit each item independently, blind to any critic output. Record the verdict per item; the scores become `AUDITED` only when every item is confirmed.

## Audit checklist (every item)

- [ ] the item carries exactly the stated defect and no other
- [ ] every number in a clean item equals the evidence
- [ ] every mechanism refers only to quantities in the evidence (no external facts)
- [ ] the expected dimension and evidence ids are the only correct ones

## Evidence cited by the items

| Metric | Value (%) |
|---|---|
| `metric:market_segment_share:L4` | 22.3470 |
| `metric:market_segment_share:P4` | 17.1700 |
| `metric:non_segment_share:L4:tesla` | 0.0000 |
| `metric:non_segment_share:P4:tesla` | 0.0000 |
| `metric:segment_mix:L4:tesla` | 100.0000 |
| `metric:segment_mix:P4:tesla` | 100.0000 |
| `metric:segment_share:L4:byd` | 4.7522 |
| `metric:segment_share:L4:volkswagen` | 7.7048 |
| `metric:segment_share:P4:byd` | 1.7122 |
| `metric:share:L4:tesla` | 2.3830 |

## Set S1

### S1:H1: PLANTED DEFECT: misencoded_test

- **Statement:** byd's segment share will stay above volkswagen's as the segment grows.
- **Mechanism:** growth of the segment's share of the market → a larger segment raises the brand's segment units (segment_share, increase)
- **Assumptions:** segment growth continues
- **Supporting evidence:** `metric:segment_share:L4:byd`, `metric:segment_share:L4:volkswagen`
- **Implications:** segment_share_gap byd-volkswagen above_reference vs `metric:segment_share:L4:volkswagen`
- **Expected classification:** test_validity; expected severity at least **high**
- **Auditor verdict:** [ ] confirmed  [ ] wrong defect or not clean  (notes: ____ )

### S1:H2: PLANTED DEFECT: contradicted_assumption

- **Statement:** tesla's total share is supported by substantial sales outside the segment.
- **Mechanism:** growth of the segment's share of the market → a larger segment raises the brand's segment units (share, increase)
- **Assumptions:** tesla sells substantial volumes outside the segment
- **Supporting evidence:** `metric:share:L4:tesla`
- **Implications:** share tesla decrease_vs_L4
- **Expected classification:** factual_contradiction; expected severity at least **high**; must cite one of `metric:non_segment_share:L4:tesla`, `metric:non_segment_share:P4:tesla`, `metric:segment_mix:L4:tesla`, `metric:segment_mix:P4:tesla`
- **Auditor verdict:** [ ] confirmed  [ ] wrong defect or not clean  (notes: ____ )

### S1:H3: PLANTED DEFECT: wrong_number

- **Statement:** byd's segment share reached 7.13% in the latest window as the segment grew.
- **Mechanism:** growth of the segment's share of the market → a larger segment raises the brand's segment units (segment_share, increase)
- **Assumptions:** segment growth continues
- **Supporting evidence:** `metric:segment_share:L4:byd`, `metric:market_segment_share:L4`
- **Implications:** segment_share byd increase_vs_L4
- **Expected classification:** numerical_accuracy or factual_contradiction; expected severity at least **high**; must cite one of `metric:segment_share:L4:byd`
- **Auditor verdict:** [ ] confirmed  [ ] wrong defect or not clean  (notes: ____ )

### S1:H4: CLEAN CONTROL

- **Statement:** byd's segment share moved from 1.71% to 4.75% while the segment's market share moved from 17.17% to 22.35%.
- **Mechanism:** growth of the segment's share of the market → a larger segment raises the brand's segment units (segment_share, increase)
- **Assumptions:** segment growth continues
- **Supporting evidence:** `metric:segment_share:L4:byd`, `metric:segment_share:P4:byd`, `metric:market_segment_share:L4`, `metric:market_segment_share:P4`
- **Implications:** market_segment_share market increase_vs_L4; segment_share byd increase_vs_L4
- **Expected:** no defect claim at medium or high severity (generic alternative-explanation caveats are allowed)
- **Auditor verdict:** [ ] confirmed  [ ] wrong defect or not clean  (notes: ____ )

## Set S2

### S2:H1: PLANTED DEFECT: unsupported_detail

- **Statement:** A wave of recent model launches by volkswagen explains its segment movement.
- **Mechanism:** recent model launches → new models attract buyers (segment_share, increase)
- **Assumptions:** volkswagen launched several new models this year
- **Supporting evidence:** `metric:segment_share:L4:volkswagen`
- **Implications:** segment_share volkswagen increase_vs_L4
- **Expected classification:** unsupported_detail; expected severity at least **medium**
- **Auditor verdict:** [ ] confirmed  [ ] wrong defect or not clean  (notes: ____ )

### S2:H2: CLEAN CONTROL

- **Statement:** tesla sells only in the segment (100.00% segment mix), so its total share moves with the segment.
- **Mechanism:** growth of the segment's share of the market → a larger segment raises the brand's segment units (share, increase)
- **Assumptions:** tesla's segment mix stays at 100.00%
- **Supporting evidence:** `metric:segment_mix:L4:tesla`, `metric:market_segment_share:L4`
- **Implications:** segment_mix tesla increase_vs_L4
- **Expected:** no defect claim at medium or high severity (generic alternative-explanation caveats are allowed)
- **Auditor verdict:** [ ] confirmed  [ ] wrong defect or not clean  (notes: ____ )

### S2:H3: PLANTED DEFECT: trend_only_test

- **Statement:** Segment growth causes volkswagen's segment movement, so it will continue.
- **Mechanism:** growth of the segment's share of the market → a larger segment raises the brand's segment units (segment_share, increase)
- **Assumptions:** segment growth is the cause
- **Supporting evidence:** `metric:segment_share:L4:volkswagen`, `metric:market_segment_share:L4`
- **Implications:** segment_share volkswagen increase_vs_L4
- **Expected classification:** causal_identification or test_validity; expected severity at least **medium**
- **Auditor verdict:** [ ] confirmed  [ ] wrong defect or not clean  (notes: ____ )

## Sign-off

- Auditor: ____  Date: ____  All items confirmed: [ ] yes
- After sign-off, set `audit.status` to `audited` (and the auditor) in the JSON. The scores are then labelled AUDITED.
