---
status: active
created_at: 2026-10-11
last_updated: 2026-10-11
---

# Engine Coverage

Maps concepts from the [Danish Mortgage Concepts Guide](danish-mortgage-concepts.md) to
what the boligregner-os engine actually implements. The guide describes the Danish
mortgage system; this document tracks the engine's scope.

## Loan types

| Guide concept | Engine `LoanType` | Status |
|---|---|---|
| Fixed-rate bonds (obligationslån) | `FIXED` | ✅ Implemented |
| Cash loans (kontantlån) | `FIXED` with `coupon_rate` | ✅ Implemented |
| F1 (1-year variable) | `F1` | ✅ Implemented |
| F3 (3-year variable) | `F3` | ✅ Implemented |
| F5 (5-year variable) | `F5` | ✅ Implemented |
| F10 (10-year variable) | — | ❌ Not implemented |
| T loan (fixed payment, variable maturity) | `T` | ✅ Implemented |
| CITA (FlexKort®) | `CITA` | ✅ Implemented |
| CIBOR (being phased out) | `CIBOR` | ✅ Implemented |
| DESTR (replacing CIBOR) | `DESTR` | ✅ Implemented |
| Guarantee loans (garantilån) | — | ❌ Not implemented |
| Priority loans (prioritetslån) | — | ❌ Not implemented |

## Core calculations

| Guide concept | Engine implementation | Status |
|---|---|---|
| Hovedstol derivation | `_hovedstol_for_provenu()` — kontantlån vs obligationslån dispatch | ✅ |
| Annuity amortization | `_amortize()` — level payment over term | ✅ |
| Quarterly payments (default) | `payments_per_year=4` on `LoanSpec` | ✅ |
| Monthly payments (bank loans) | `payments_per_year=12` on `LoanSpec` | ✅ |
| ÅOP via IRR | `_aap()` — Newton-Raphson IRR on actual cash flows | ✅ |
| Periodeomkostning | `_horizon_scenarios()` — ydelse + indfrielse − provenu after tax | ✅ |
| 5-year horizon with rate shocks | `_horizon_scenarios()` — −2%, 0%, +2% scenarios | ✅ |
| Tax deduction (rentefradrag) | Applied to interest + bidrag at marginal tax rate | ✅ |
| Bidrag tax treatment | All bidrag is tax-deductible | ✅ |

## Loan features

| Guide concept | Engine field | Status |
|---|---|---|
| Interest-only (afdragsfrihed) | `LoanSpec.interest_only_years` | ✅ Implemented |
| Deep-discount bonds | `LoanSpec.coupon_rate` on FIXED | ✅ Implemented |
| T loan fixed payment | `LoanSpec.fixed_ydelse` | ✅ Implemented |
| Reference-rate loans (CITA/CIBOR/DESTR) | `reference_rate` + `margin` | ✅ Implemented |
| DESTR daily compounding | `_daily_to_monthly()` conversion | ✅ Implemented |
| Bond price at redemption | `_bond_price_with_option()` — pull-to-par model | ✅ Implemented |
| LTV-based realkredit/bank split | `ejendomsvaerdi` + `ejendomstype` on `CalculatorInput` | ✅ Implemented |
| Issue costs (pct or nominal) | `issue_costs_pct` or `issue_costs_nominal` | ✅ Implemented |
| Custom redemption price | `redemption_price` on `LoanSpec` | ✅ Implemented |

## Not implemented

| Guide concept | Notes |
|---|---|
| **F10** (10-year variable) | Not in `LoanType` enum. Add as `F10 = "f10"` — same amortization as F1/F3/F5, just a different adjustment interval. |
| **Guarantee loans** (garantilån) | Variable rate with a rate cap. Would need a new `LoanType` and cap logic in the rate path. |
| **Priority loans** (prioritetslån) | Bank loans with mortgage-grade security. Would need a new `LoanType` and LTV integration. |
| **Kursskæring** (rate-cut fee) | Fee applied at each refinancing of variable-rate loans. No field or logic in the engine. The guide describes it as a domain concept. |
| **Konvertering** (conversion) | Replacing an existing fixed-rate loan with a new one at a different coupon. The engine computes new loans from scratch, not conversions from an existing loan. |
| **Omlægning** (refinancing) | Replacing an existing loan with new alternatives. Same gap as konvertering — no "existing loan" input. |
| **LTV-based bidragssats** | Real Danish bidrag varies by `(institute, loan_type, LTV_band, afdragsfrihed)`. The engine uses a single flat `bidragssats` field. See `docs/market-data-sources.md` for the planned lookup layer. |
| **Daily bond prices** | The engine uses a static `price` on `LoanSpec`. No live market data fetching. See `docs/market-data-plan.md`. |
| **Two-layer financing** (tolagsbelåning) | Historical (2013–2014). RO bonds are no longer used. The engine supports multi-component loans but does not model the SDO/RO split specifically. |
| **Friværdi** (equity) | The guide computes equity as property value − remaining debt. The engine reports `restgæld` (remaining debt) but does not take property value as input for equity calculation (only for LTV split). |

## See also

- [Danish Mortgage Concepts Guide](danish-mortgage-concepts.md) — domain reference
- [Market Data Sources](market-data-sources.md) — research on open-access data sources
- [Market Data Implementation Plan](market-data-plan.md) — plan for live data integration (not yet implemented)
- [Correctness Gaps](correctness-gaps.md) — remaining deviations from boligregner.dk
