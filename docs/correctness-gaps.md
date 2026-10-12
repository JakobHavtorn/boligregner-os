---
status: active
created_at: 2026-10-07
last_updated: 2026-10-12
---

# Remaining Correctness Gaps vs boligregner.dk

This document is the single source of truth for measured numeric
deviations between the engine and boligregner.dk reference data.
GitHub issue #26 has been closed in favour of this document.

## Current state (main, post-PR #31)

32 reference cases, 500 tests collected (295 passed, 205 skipped).

Run the report:

```bash
uv run --extra dev python scripts/correctness_report.py
```

---

## Gap 1: F1/F5 ÅOP and ydelse deviation

### Symptoms (post-PR: fractional maturity fix)

| Case | Metric | Engine | Reference | Deviation |
|------|--------|--------|-----------|----------|
| apr2023_f1_20 | Ydelse | 16,427 | 16,464 | −0.23% |
| apr2023_f1_25 | Ydelse | 14,412 | 14,432 | −0.14% |
| apr2023_f1_30 | Ydelse | 13,096 | 13,106 | −0.08% |
| apr2023_f1_20 | ÅOP | 4.2269% | 4.1800% | +1.12% |
| apr2023_f1_25 | ÅOP | 4.2183% | 4.1600% | +1.40% |
| apr2023_f1_30 | ÅOP | 4.2125% | 4.1500% | +1.51% |
| f1_oct | Ydelse | 12,659 | 12,665 | −0.05% |
| f1_oct_6m | Ydelse | 32,853 | 32,869 | −0.05% |
| f1_oct | ÅOP | 4.2385% | 4.3600% | −2.79% |
| f1_oct_6m | ÅOP | 4.2244% | 4.3500% | −2.89% |
| f1_oct_bidrag_0038 | ÅOP | 3.6467% | 3.7700% | −3.27% |
| f1_oct_bidrag_0060 | ÅOP | 3.8748% | 4.0000% | −3.13% |
| f5_jan | Ydelse | 13,337 | 13,395 | −0.43% |
| f5_jan | ÅOP | 4.6660% | 4.7200% | −1.15% |

The fractional maturity fix eliminates the ydelse overshoot for apr2023
and f5_jan cases (previously +0.76–1.35%, now within ±0.5%). The
remaining ÅOP deviations are within the 0.5 pp test tolerance but still
show a residual pattern:

1. **apr2023 F1** (price 99.03, bidragssats 0.0038): ÅOP overshoots
   (+1.12–1.51%). The compound rate annuity on hovedstol produces a
   slightly higher IRR than boligregner.dk when price ≈ 100.
2. **f1_oct** (price 98.26, bidragssats 0.0095): ÅOP undershoots
   (−2.79% to −2.89%). When price < 100, kontant < hovedstol, so the
   IRR of [kontant, −compound-annuity] undershoots.
3. **f5_jan** (price 91.30, bidragssats 0.0095): ÅOP undershoots
   (−1.15%). The deep discount (price 91.30) makes the undershoot
   worse, but the shorter term (30.5y vs 31y) partially compensates.

The bidragssats isolation cases (`f1_oct_bidrag_0038` vs
`f1_oct_bidrag_0060`) confirm the ÅOP gap widens at lower bidragssats
(−3.27% at 0.0038 vs −3.13% at 0.0060), suggesting the ÅOP IRR
calculation's handling of bidrag is a contributing factor beyond
maturity alone.

### Root cause

**Fractional maturity** (RESOLVED): `maturity_years` was
integer-only, so 30.5-year loans were truncated to 30 years (120
quarters instead of 122). Now `maturity_years` accepts `Decimal` and
the period count uses `_qceil(maturity_years * ppy)`.

**ÅOP bidrag handling** (investigated, residual within tolerance):
The ÅOP IRR uses the compounded rate path (rate + bidragssats) for
amortization with kontant as the disbursement. This is equivalent to
plain-rate annuity + declining bidrag on remaining balance. Extensive
testing (~20 alternative cashflow models) found that:
- Plain rate + flat bidrag on hovedstol overshoots by +6–8%.
- Plain rate + no bidrag undershoots by −8 to −25%.
- Compound rate on obligationshovedstol gives +0.5–16% (inconsistent).
- The current compound-rate-on-hovedstol model is the closest to
  boligregner.dk across all cases (−3.3% to +1.5%, all within 0.5 pp
  absolute tolerance).

The residual deviation stems from the gap between the compounded-rate
annuity (declining bidrag) and boligregner.dk's actual ydelse (plain
annuity + flat bidrag). When price < 100, the kontant disbursement is
less than hovedstol, so the IRR of the compound annuity undershoots.
When price ≈ 100, the compound annuity slightly exceeds the actual
ydelse, so the IRR overshoots.

### Status

Partially resolved. Fractional maturity support eliminates the ydelse
deviation. The residual ÅOP deviation (−3.3% to +1.5% relative, all
within 0.5 pp absolute tolerance) is the best achievable with the
current IRR model. Further improvement would require matching
boligregner.dk's exact ÅOP cashflow construction, which is not publicly
documented.

---

---

## Gap 3: CITA horizon rente (+2.3 to +2.8%)

| Case | Engine | Reference | Deviation |
|------|--------|-----------|-----------|
| cita_30_6m | 744,381 | 723,935 | +2.82% |
| cita_30 | 244,521 | 239,012 | +2.30% |

Ydelse and ÅOP are tight (ydelse +0.04%, ÅOP +0.46–0.75%).

### Root cause

The CITA rate path model (reference + margin, no compounding) likely
differs from boligregner.dk's internal rate projection over the 5-year
horizon. Boligregner.dk may use a forward-rate or swap-based projection
for CITA over the horizon period.

### Status

Open. Lower priority — ydelse and ÅOP are tight.

---

## Priority

1. **Gap 1** (F1/F5 maturity + ÅOP) — partially resolved:
   fractional maturity fixed; residual ÅOP within tolerance.
2. **Gap 3** (CITA horizon rente) — lowest impact, horizon-only.

---

## Resolved gaps

### Kontantlån hovedstol (RESOLVED in #24)

Flexlån (F1/F3/F5), T-lån, and reference-rate loans (CITA/CIBOR/DESTR)
are kontantlån — hovedstol equals kursværdi at par, not the inflated
obligationshovedstol. Fixed by `_is_kontantlaan()` +
`_hovedstol_for_provenu()` dispatch. Hovedstol now matches exactly
(0.00%) for all kontantlån types.

### Deep-discount bonds (RESOLVED in #24)

When `coupon_rate` is set on a FIXED loan, the engine treats it as a
kontantlån. `fixed_1pct_6m` (price 64.03) matches with ydelse −0.02%,
ÅOP −0.17%, restgæld −0.01%.

### Bidrag tax treatment (RESOLVED in #24)

All bidrag is tax-deductible: `rente_total += (comp_interest +
bidrag_total) * (1 - tax_rate)` for all loan types.

### Split mode (RESOLVED in #25)

Hardcoded split mode: annuity at nominal rate, bidrag charged
separately, bidrag tax-deductible. ÅOP uses compounded rate path for
IRR. Matches boligregner.dk's behavior.

### T-lån ÅOP bidrag (RESOLVED in #36)

T-lån ÅOP previously excluded bidrag from the IRR cashflow stream,
undershooting by −9 to −10%. The fix amortizes the loan using the
nominal amortization rate (excluding bidragssats) to obtain a
declining balance, then charges bidrag on the post-payment outstanding
balance each period. This matches boligregner.dk's method of computing
bidrag on a declining balance rather than a flat charge on the
original hovedstol.

| Case | Before | After | Reference |
|------|--------|-------|----------|
| apr2023_tlaan_21 | 4.0329% (−9.17%) | 4.5226% (+1.86%) | 4.4400% |
| apr2023_tlaan_22 | 3.9906% (−9.71%) | 4.4828% (+1.42%) | 4.4200% |
| apr2023_tlaan_23 | 3.9747% (−9.87%) | 4.4679% (+1.31%) | 4.4100% |
| apr2023_tlaan_24 | 3.9458% (−10.32%) | 4.4403% (+0.92%) | 4.4000% |
| apr2023_tlaan_25 | 3.9839% (−9.46%) | 4.4754% (+1.71%) | 4.4000% |
| apr2023_tlaan_30 | 3.9260% (−9.95%) | 4.4215% (+1.41%) | 4.3600% |

All T-lån ÅOP deviations reduced from −9/−10% to within +2% of
reference. Ydelse remains exact (0.00%). All 295 tests pass.
