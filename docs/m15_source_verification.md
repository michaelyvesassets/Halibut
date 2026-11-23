# M15: source verification and data feasibility

Verified 2026-11-23 from this environment with anonymous requests (no keys).
"Verified" means the endpoint returned the stated data or document during
this check. Licence statements cite the publisher's own pages.

## Decision

| Pilot | Decision | Reason |
|---|---|---|
| US coffee vs matcha | **No-go for empirical calibration.** Keep as a supply and attention proxy track only. | No US consumption or purchase data for matcha. No US tariff line for matcha. The best matcha series is Japanese *exports*, which begins only in January 2019. |
| **Norway new-car powertrain displacement** (battery-electric vs hybrid vs fossil) | **Go** | Actual purchases (first-time registrations), monthly from 1995M01 to 2026M10 with no gaps. A near-complete displacement is observed. Municipal geography. CC BY 4.0. No API key. |

## Verified sources

### Norway pilot (Statistics Norway, PxWeb API v0, `data.ssb.no/api/v0`)

Licence: CC BY 4.0 with attribution to Statistics Norway
(<https://www.ssb.no/en/diverse/lisens>). The API is open and needs no
registration. A rate limit of about 30 queries per 60 s is mentioned in the
API guide and must be re-checked in M17. SSB is moving to PxWebApi v2, so
v0 is a migration risk.

| Table | Content | Coverage | Evidence class | Notes |
|---|---|---|---|---|
| 14020 | First-time registered vehicles by registration type (new / second-hand import), fuel (electric/zero-emission, fossil, hybrid, other) and vehicle type | monthly, 1995M01–2026M10 (382 months), national; 0 missing values for new private cars | **transaction** (registrations ≈ purchases) | `updated` 2026-11-03: published about 3 days after month end |
| 07849 | Registered vehicles (stock) by region (994 regions incl. municipalities), use, fuel and vehicle type | annual, 2008–2025 | stock (fleet) | geographic resolution |
| 09654 | Retail engine-fuel prices (95-octane petrol, diesel), NOK/litre | monthly, 1986M08–2026M10 | price | operating-cost covariate |
| 14491 / 14493 | Household electricity prices | quarterly from 2012 / annual 2012–2025 | price | EV operating-cost covariate |

Observed battery-electric share of new private cars (table 14020, computed
from the downloaded data):

| Year | BEV share |
|---|---|
| 2010 | 0.3% |
| 2013 | 5.5% |
| 2015 | 17.1% |
| 2018 | 31.2% |
| 2020 | 54.3% |
| 2022 | 79.3% |
| 2024 | 88.9% |
| 2025 | 95.9% |

In 2025 the fossil share was 1.0% and hybrids 3.1%.

### Coffee vs matcha track

| Source | Verified finding | Evidence class | Access |
|---|---|---|---|
| BLS API v1 | `APU0000717311` (US average price, ground coffee): 105 of 120 months present for 2015–2024, so it has gaps. `CUUR0000SEFP01` (CPI coffee): 120/120 | price | no key (v1 has daily limits) |
| FRED / ALFRED | **Timed out from this environment** (HEAD requests answered; data requests did not) | price, with release history | API key; needs re-testing elsewhere |
| US Census intl. trade API | Returns **"Missing Key"**: a key is now required | trade (imports) | free key |
| UN Comtrade public preview | Japan → US, HS 090210, 2023: USD 47.7M, 1,006 t | trade | no key for preview; key for bulk |
| Japan customs | Export code **0902.10-100 (powdered green tea, including matcha) has existed only since the January 2019 revision.** In 2024 Japan exported 8,798 t of green tea in total, worth ¥36.4bn ([Japan Customs, Osaka](https://www.customs.go.jp/osaka/toukei/pdf/tokushu_202512.pdf)) | trade (exporter side) | e-Stat API needs an `appId` |
| USDA FAS PSD (coffee) | US domestic consumption 1960–2026 in thousand 60-kg bags. Recent market years are USDA projections, not observations | consumption balance (derived) | public domain, no key |
| Wikipedia pageviews | "Matcha" and "Coffee", monthly from 2015-07 to 2025-10 (124 months) | **proxy: attention** | CC0 / open, no key |

## Why coffee vs matcha is no-go

- **No observed consumer purchases.** There is no public US matcha sales or
  consumption series. Scanner data (NielsenIQ, Circana) is commercial.
- **Matcha cannot be isolated on the US side.** US imports under HTS 0902.10
  mix matcha with other green tea (no primary-source US matcha line was
  found).
- **The best matcha series is exporter-side and short.** Japanese powdered
  green-tea exports run from 2019 only, about 80 monthly points. It covers
  only Japanese-origin product and includes food-ingredient use and stock
  building.
- **Most behavioural parameters can't be identified**, including loyalty,
  switching and social influence, and so are retail price elasticities.

These sources remain useful for a *supply and attention* description of the
matcha trend. They cannot support calibration of the consumer model.

## What the Norway pilot can and cannot measure

**Measurable:**
- the national monthly purchase mix by powertrain;
- the timing and shape of a near-complete displacement;
- seasonality;
- municipal-level fleet diffusion (annual);
- fuel and electricity price covariates;
- second-hand imports as a separate channel.

**Not observable from these tables:**
- individual buyers, so no switching between powertrains per household;
- car model prices and the EV–ICE purchase price gap;
- policy incentives (VAT exemption, tolls, registration tax). These must be
  coded as a separately sourced, cited *policy-event* dataset;
- charging infrastructure (a candidate extra source, to verify);
- income and segment composition;
- model-level supply constraints;
- vintage (revision) history, which SSB does not publish. Availability dates
  will be the observed publication date where it is known. Otherwise a
  conservative estimate: month end plus a documented lag. Halibut snapshots
  each retrieval itself from now on.

## Modelling implication (requires approval in M19)

Cars are durable goods: each household is in the market roughly once every
several years, not every week. The engine would need an **optional purchase
hazard**, the probability that a consumer is in the market in a given tick.
It would default to off, so all v1/v2 configs and golden results are
unchanged. With it, Halibut's displacement mechanics (price, quality,
availability, social influence, drift) apply unchanged to who buys what
*when they buy*.

## API keys

- **Norway pilot:** none required.
- Optional, for the coffee-vs-matcha proxy track:
  - US Census API key (confirmed required);
  - Japan e-Stat `appId`;
  - FRED API key (FRED was also unreachable from this environment);
  - UN Comtrade key, for more than preview queries.

Keys should be registered by the project owner and supplied through
environment variables. They are never committed.
