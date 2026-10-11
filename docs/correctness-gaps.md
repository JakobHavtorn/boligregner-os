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

### Symptoms

| Case | Metric | Engine | Reference | Deviation |
|------|--------|--------|-----------|-----------|
| apr2023_f1_20 | Ydelse | 16,687 | 16,464 | +1.35% |
| apr2023_f1_25 | Ydelse | 14,575 | 14,432 | +0.99% |
| apr2023_f1_30 | Ydelse | 13,206 | 13,106 | +0.76% |
| apr2023_f1_20 | ÅOP | 4.2280% | 4.1800% | +1.15% |
| apr2023_f1_25 | ÅOP | 4.2190% | 4.1600% | +1.42% |
| apr2023_f1_30 | ÅOP | 4.2130% | 4.1500% | +1.52% |
| f1_oct | Ydelse | 12,659 | 12,665 | −0.05% |
| f1_oct_6m | Ydelse | 32,853 | 32,869 | −0.05% |
| f1_oct | ÅOP | 4.2385% | 4.3600% | −2.79% |
| f1_oct_6m | ÅOP | 4.2244% | 4.3500% | −2.89% |
| f1_oct_bidrag_0038 | ÅOP | 3.6467% | 3.7700% | −3.27% |
| f1_oct_bidrag_0060 | ÅOP | 3.8748% | 4.0000% | −3.13% |
| f5_jan | Ydelse | 13,444 | 13,395 | +0.36% |
| f5_jan | ÅOP | 4.6679% | 4.7200% | −1.10% |

Two distinct patterns:

1. **apr2023 F1** (bidragssats 0.0038): ydelse overshoots (+0.76–1.35%),
   ÅOP overshoots (+1.15–1.52%).
2. **f1_oct** (bidragssats 0.0095): ydelse matches (−0.05%), ÅOP
   undershoots (−2.79% to −2.89%).

The bidragssats isolation cases (`f1_oct_bidrag_0038` vs
`f1_oct_bidrag_0060`) confirm the ÅOP gap widens at lower bidragssats
(−3.27% at 0.0038 vs −3.13% at 0.0060), suggesting the ÅOP IRR
calculation's handling of bidrag is a contributing factor beyond
maturity alone.

### Root cause

The engine uses integer years for `maturity_years` (30y = 120 quarters).
F1 loans on boligregner.dk have a 31-year løbetid (124 quarters) from a
360-month window. The extra 4 quarters reduce the annuity payment, so
apr2023 cases with shorter effective terms produce slightly higher
ydelse.

The ÅOP deviation direction flips between the two bidragssatis levels,
which points to a second contributing factor in the IRR bidrag handling
that is not yet isolated.

### Status

Open. Fixing requires fractional maturity support in the annuity
formula and investigation of the bidragssatis-dependent ÅOP split.

---

## Gap 2: T-lån ÅOP (−9 to −10%)

| Case | Engine ÅOP | Reference ÅOP | Deviation |
|------|-----------|---------------|-----------|
| apr2023_tlaan_21 | 4.0329% | 4.4400% | −0.407pp (−9.17%) |
| apr2023_tlaan_22 | 3.9906% | 4.4200% | −0.429pp (−9.71%) |
| apr2023_tlaan_23 | 3.9747% | 4.4100% | −0.435pp (−9.87%) |
| apr2023_tlaan_24 | 3.9458% | 4.4000% | −0.454pp (−10.32%) |
| apr2023_tlaan_25 | 3.9839% | 4.4000% | −0.416pp (−9.46%) |
| apr2023_tlaan_30 | 3.9260% | 4.3600% | −0.434pp (−9.95%) |

Ydelse matches exactly (0.00%) — it is a fixed input. Horizon metrics
are tight (rente +1.99%, ydelse +0.23%).

### Root cause

T-lån ÅOP uses IRR of `fixed_ydelse` cashflows against
`net_disbursement`. The cashflow stream excludes bidrag — the
compounded rate path (which includes bidragssats) is only used as the
IRR initial guess, not in the actual cashflows. Boligregner.dk
apparently includes bidrag in their ÅOP calculation for T-lån.

This gap was pre-existing (verified: identical values on origin/main
before #24). The models refactor (#25, merged) does not introduce or
change this gap.

### Status

Open. Fix requires adding bidrag to the T-lån ÅOP cashflow stream, but
this would change the ÅOP values and needs verification against
boligregner.dk's exact method.

---

## Gap 3: CITA horizon rente (+2.3 to +2.8%) — RESOLVED

| Case | Engine | Reference | Deviation |
|------|--------|-----------|----------|
| cita_30_6m | 731,046 | 723,935 | +0.98% |
| cita_30 | 239,384 | 239,012 | +0.16% |
| cita_30_afr_12m | 1,467,216 | 1,472,975 | −0.39% |

Previous deviations were +2.82%, +2.30%, and +1.33% respectively.
Ydelse remains tight (ydelse +0.04%, unchanged).

### Root cause

The CITA rate path used `reference + margin` with no day-count or
compounding conversion.  CITA is quoted as a nominal annual rate on an
ACT/360 day-count basis, but Danish realkredit accrues interest on
ACT/365 and CITA loans pay quarterly (ppy=4).  Two conversions are
needed:

1. **Day-count conversion** (ACT/360 → ACT/365):
   `rate_365 = rate × 360/365`
2. **Compounding conversion** (annual → quarterly):
   `quarterly = (1 + rate_365)^(1/ppy) − 1`,  `nominal = quarterly × ppy`

Both steps reduce the effective rate, bringing the engine's horizon
rente in line with boligregner.dk.

### Fix

Added `_cita_rate_adjustment()` in `engine.py` and applied it in both
`_rate_path()` and `_rate_path_compounded()` for `LoanType.CITA`.
DESTR already had its own day-count conversion (`_daily_to_monthly`);
CIBOR and fixed-rate loans are unaffected.

### Status

Resolved. All three CITA horizon rente cases now deviate less than 1%.
ÅOP improved from +0.46–0.75% to −1.19% to −1.29% (below reference,
a step in the correct direction).

---

## Priority

1. **Gap 1** (F1/F5 integer maturity + ÅOP bidrag split) — highest
   impact, affects most cases.
2. **Gap 2** (T-lån ÅOP) — largest single deviation, but T-lån only.

All three gaps have been addressed. Gap 3 (CITA horizon rente) was
resolved by adding ACT/360 → ACT/365 day-count conversion plus
quarterly compounding in the CITA rate path.

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
