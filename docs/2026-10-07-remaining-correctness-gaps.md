# Remaining Correctness Gaps vs boligregner.dk

## Current state (PR #25, post-rebase onto #24)

21 reference cases, 219 tests passing. Correctness report:
`uv run --extra dev python scripts/correctness_report.py`

---

## Gap 1: F1 integer-only maturity (ydelse +0.76–1.35%, ÅOP −1.15–2.89%)

### Symptoms

F1 loans overshoot ydelse on apr2023 cases (+0.76% to +1.35%) but match
closely on the new f1_oct case (−0.05%). ÅOP runs consistently negative
(−1.15% to −2.89%).

### Root cause

The engine uses integer years for `maturity_years` (30y = 120 quarters).
F1 loans on boligregner.dk have a 31-year løbetid (124 quarters) from a
360-month window. The extra 4 quarters reduce the annuity payment, so our
slightly higher ydelse comes from a shorter effective term.

### Status

Open. Fixing requires fractional maturity support in the annuity formula.

---

## Gap 2: T-lån ÅOP (−9 to −10%)

### Symptoms

| Case | Engine ÅOP | Reference ÅOP | Gap |
|------|-----------|---------------|-----|
| apr2023_tlaan_21 | 4.0329% | 4.4400% | −0.407pp (−9.17%) |
| apr2023_tlaan_24 | 3.9458% | 4.4000% | −0.454pp (−10.32%) |
| apr2023_tlaan_30 | 3.9260% | 4.3600% | −0.434pp (−9.95%) |

Ydelse matches exactly (0.00%) — it's a fixed input. Horizon metrics are
tight (rente +1.99%, ydelse +0.23%).

### Root cause

T-lån ÅOP uses IRR of `fixed_ydelse` cashflows against `net_disbursement`.
The cashflow stream excludes bidrag — the compounded rate path (which
includes bidragssats) is only used as the IRR initial guess, not in the
actual cashflows. Boligregner.dk apparently includes bidrag in their ÅOP
calculation for T-lån.

This gap is pre-existing in #24 (verified: identical values on origin/main).
The models refactor (#25) does not introduce or change this gap.

### Status

Open. Fix requires adding bidrag to the T-lån ÅOP cashflow stream, but
this would change the ÅOP values and needs verification against
boligregner.dk's exact method.

---

## Gap 3: CITA horizon rente (+2.82%)

### Symptoms

CITA_30_6m: ydelse +0.04%, ÅOP +0.46%, but horizon rente +2.82% (20,445 DKK).

### Root cause

The CITA rate path model (reference + margin, no compounding) likely
differs from boligregner.dk's internal rate projection over the 5-year
horizon. Boligregner.dk may use a forward-rate or swap-based projection
for CITA over the horizon period.

### Status

Open. Lower priority — ydelse and ÅOP are tight.

---

## Gap 4: F5 ydelse (+0.36%, ÅOP −1.10%)

Same root cause as Gap 1 (integer-only maturity). F5 has 30y = 120
quarters; boligregner.dk may use a slightly different term.

### Status

Open. Same fix as Gap 1.

---

## Resolved gaps

### Kontantlån hovedstol (RESOLVED in #24)

Flexlån (F1/F3/F5), T-lån, and reference-rate loans (CITA/CIBOR/DESTR) are
kontantlån — hovedstol equals kursværdi at par, not the inflated
obligationshovedstol. Fixed by `_is_kontantlaan()` + `_hovedstol_for_provenu()`
dispatch. Hovedstol now matches exactly (0.00%) for all kontantlån types.

### Deep-discount bonds (RESOLVED in #24)

When `coupon_rate` is set on a FIXED loan, the engine treats it as a
kontantlån. `fixed_1pct_6m` (price 64.03) matches with ydelse −0.02%,
ÅOP −0.17%, restgæld −0.01%.

### Bidrag tax treatment (RESOLVED in #24)

All bidrag is tax-deductible: `rente_total += (comp_interest + bidrag_total)
* (1 - tax_rate)` for all loan types.

### Split mode (RESOLVED in #25)

Hardcoded split mode: annuity at nominal rate, bidrag charged separately,
bidrag tax-deductible. ÅOP uses compounded rate path for IRR. Matches
boligregner.dk's behavior.
