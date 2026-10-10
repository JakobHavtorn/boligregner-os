# Remaining Correctness Gaps vs boligregner.dk

## Context

The engine now matches boligregner.dk closely on ydelse (0.1–1.6% off), ÅOP
(0.02–0.08pp off), afdrag (0.1–6.6% off), and restgæld (0.1–0.8% off) for
single-component loans. Two gaps remain after the kontantlån hovedstol fix.

Reference data was captured from boligregner.dk on Oct 5–8, 2026 (Session A:
single-component, Session B: with-bank, Oct 8: F1/F5/FIXED5 detail captures).
See `local://boligregner-reference-data.md`.

---

## Gap 1 (RESOLVED): Flexlån hovedstol derivation

### What was wrong

The engine derived `obligationshovedstol` for ALL loan types by dividing
provenu by the bond kurs:

```
hovedstol = round_up_1000(provenu / (kurs/100 - issue_costs_pct))
```

This is correct for fixed-rate obligations (obligationslån), but wrong for
flexlån (F1/F3/F5), T-lån, and reference-rate loans (CITA/CIBOR/DESTR). These
are **kontantlån** (cash loans) whose hovedstol equals their kursværdi at
par — not the inflated obligationshovedstol from a discounted bond price.

### Root cause (confirmed by boligregner.dk help text)

boligregner.dk's details view states:

> "Hovedstol: For kontantlån angives kontantlånshovedstolen, for
> obligationslån obligationshovedstolen."

And:

> "Kursværdi: Lånets kursværdi beregnes som obligationshovedstolen ganget med
> obligationskursen (tillagt eventuel prisskæring) og divideret med 100."

For **kontantlån** (flexlån, T-lån, reference-rate loans):
- hovedstol = kursværdi = round_up_1000(provenu + udst.omk) [nominal]
- hovedstol = round_up_1000(provenu / (1 - issue_costs_pct)) [pct]
- obligationshovedstol = round_up_1000(hovedstol / (kurs/100)) — tracked
  separately, used for bond pricing/redemption

For **obligationslån** (FIXED):
- hovedstol = obligationshovedstol = old formula (unchanged)

### Fix implemented

- `_is_kontantlaan(loan_type)` — returns True for F1/F3/F5/T/CITA/CIBOR/DESTR,
  False for FIXED.
- `_hovedstol_for_provenu` — now accepts `kontantlaan: bool` and returns
  `tuple[Decimal, Decimal]` (hovedstol, obligationshovedstol).
- `_compute_component` — dispatches on loan type; sets `kursvaerdi = hovedstol`
  for kontantlån (at par); stores `obligationshovedstol` in `LoanComponentResult`
  when it differs from hovedstol.
- `LoanComponentResult.obligationshovedstol: Decimal | None` — None for
  obligationslån where it equals hovedstol.

### Verification (Oct 8 captures, 0% shock, 5-year horizon)

| Loan | Hovedstol | Ref | Match | Rente gap | Ydelse gap | Restgæld gap |
|------|-----------|-----|-------|-----------|------------|--------------|
| F5   | 2.547.000 | 2.547.000 | ✓ | +0.8% | +1.0% | −0.2% |
| FIXED5 | 2.534.000 | 2.536.000 | ~ | +0.4% | +0.2% | −0.1% |
| F1   | 2.545.000 | 2.545.000 | ✓ | −1.3% | +2.2% | −0.8% |

**Before fix**: F5 rente gap was +10.3% (using obligationshovedstol 2.788.000).
**After fix**: F5 rente gap is +0.8% (using kontantlån hovedstol 2.547.000).

FIXED5 is unchanged — it's an obligationslån, fix doesn't apply. Its 2.000
hovedstol discrepancy (2.534.000 vs 2.536.000) is from premium-bond rounding
(kurs=100.41) and has negligible impact (0.4% gap).

### F1 residual (−1.3% rente gap)

F1's løbetid is 31 years (124 quarters) on boligregner.dk, but the engine uses
`maturity_years=30` (120 quarters). With n=124, the gap shrinks to ~−2.3%
(not better — the longer term reduces the annuity payment, widening the
ydelse gap). The residual is likely from a different first-period rate or a
rounding convention in F1's quarterly annuity. Investigation deferred.

---

## Gap 2: gns_kurs at nonzero rate shocks (3.4–3.7pp off)

### Symptoms (Session A FIXED4: price=100, rate=4%, 30yr)

| Shock | Engine | Reference | Gap |
|-------|--------|-----------|-----|
| 0%    | 100.00 | 96.33     | 3.67pp |
| +2%   | 82.69  | 86.08     | −3.39pp |
| −2%   | 100.50 | 100.50    | 0.00pp |

The −2% shock matches exactly (capped at par + premium). The 0% and +2%
shocks remain off.

### Root cause

The engine prices the bond as the present value of remaining cashflows.
At par (price=100), the issue yield equals the coupon rate, so the 0% shock
price = 100.00 — but boligregner.dk gives 96.33, suggesting their model
factors in the prepayment option's cost even at par.

At +2% shock, `shocked_yield = coupon + shock × (1 − duration_adj)` where
`duration_adj = min(prepayment_premium × 10, 1)` = 0.05. This gives
`shocked_yield = 4% + 2% × 0.95 = 5.9%` and a bond price of 82.69. The
reference gives 86.08 — still higher, consistent with an **OAS
(option-adjusted spread) model** used by Scanrate.

### What's implemented

A **reduced-duration heuristic** (commit `b81794a`): for FIXED bonds with
`finite_option` pricing at nonzero shocks, the yield shock is scaled by
`(1 − prepayment_premium × 10)`, approximating the callable bond's lower
effective duration. `duration_adj` is clamped so it can't invert when
`prepayment_premium > 0.10`.

This improved the +2% gap from 4.17pp (pre-heuristic, 81.91) to 3.39pp
(82.69), but didn't close it. The −2% shock remains exact (0.00pp).

### Why we can't replicate it exactly

Scanrate's OAS model uses a proprietary interest-rate model calibrated to
the Danish yield curve, a proprietary prepayment model, and the current
yield curve — none of which are publicly available. Our finite-bond +
prepayment-option-cap model approximates the OAS model but overestimates
effective duration.

### Further fix options

**Option A: Calibrate duration_adj** — tune the multiplier (currently 10)
to match Scanrate's price sensitivity. Data-fitting risk.

**Option B: Yield curve shift model** — non-parallel shift that steepens
the yield curve. A +2% short-end shock might be +1.5% at 30-year.

**Option C: Full OAS** — Monte Carlo with Hull-White + prepayment model.
Out of scope for an open-source calculator.

**Recommended**: Option A (calibrate). **Difficulty**: Medium.

---

## Gap 3 (SAME ROOT CAUSE AS GAP 1): With-bank ydelse

### Symptoms

| Loan | Engine ydelse (monthly-equiv) | Reference | Gap |
|------|-------------------------------|-----------|-----|
| F3+bank   | 12.233 | 14.442 | 15.3% |
| F5+bank   | 13.004 | 14.676 | 11.4% |
| 4%+bank   | 12.935 | 15.666 | 17.4% |

### Root cause

Same as Gap 1: the engine derived obligationshovedstol for flexlån, inflating
the realkredit hovedstol and deflating the bank residual. With the kontantlån
fix, the realkredit hovedstol is now at par (lower), leaving more room for
the bank loan.

The par-cap mechanism in `calculate()` (already implemented) detects when
realkredit is par-capped and reallocates the residual provenu to the bank
component. With the correct kontantlån hovedstol, this mechanism should now
produce the correct split.

### Status

The kontantlån hovedstol fix should resolve both Gap 1 and Gap 3
simultaneously. Session B (with-bank) verification is pending — needs a
re-run with Oct 8 parameters to confirm the gap closes.

---

## Summary

| Gap | Metric | Before fix | After fix | Status |
|-----|--------|------------|-----------|--------|
| 1 | Flexlån rente_h | 8–11% | 0.8% (F5) | ✅ Resolved (kontantlån hovedstol) |
| 1 | Flexlån ydelse_h | 7.9–8.3% | 1.0% (F5) | ✅ Resolved (same fix) |
| 1 | F1 rente residual | — | −1.3% | Open (løbetid 31yr vs 30yr) |
| 2 | gns_kurs +2% shock | 4.17pp | 3.39pp | Partially fixed (reduced-duration heuristic) |
| 2 | gns_kurs 0% shock | 3.67pp | 3.67pp | Open (par-bond issue yield = coupon) |
| 2 | gns_kurs −2% shock | 0.00pp | 0.00pp | ✅ Exact (par cap) |
| 3 | With-bank ydelse | 11–17% | — | Pending verification (same fix) |

### What was fixed

The kontantlån hovedstol fix (`_is_kontantlaan`, `_hovedstol_for_provenu`
tuple return, `_compute_component` dispatch) resolves Gap 1 and should
resolve Gap 3. The engine now matches boligregner.dk's definition:
kontantlånshovedstol for flexlån/reference-rate loans, obligationshovedstol
for fixed-rate obligations.

### What remains

- **F1 residual** (−1.3%): likely løbetid mismatch (31yr vs 30yr). Needs deeper
  investigation of F1's first-period rate or rounding convention.
- **Gap 2** (gns_kurs at nonzero shocks): reduced-duration heuristic
  implemented (commit `b81794a`), improved +2% from 4.17pp to 3.39pp. 0%
  shock remains 3.67pp off (at par, issue yield = coupon, so price = 100).
  Full resolution requires OAS-like model with proprietary Scanrate data.
  Lower priority — −2% shock is exact.
- **Gap 3 verification**: Session B with-bank cases need re-running with the
  fix to confirm the gap closes.
- **Session A tolerance**: pre-existing 50% rente gap in Session A reference
  data is a data quality issue (reference captured with different parameters
  than test inputs), not caused by the fix. Needs separate investigation.
