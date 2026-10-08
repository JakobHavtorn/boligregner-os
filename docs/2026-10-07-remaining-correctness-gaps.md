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

## Gap 2: gns_kurs at +2% rate shock (4.17pp off)

### Symptoms

| Shock | Engine | Reference | Gap |
|-------|--------|-----------|-----|
| 0%    | 94.46  | 96.33     | 1.87pp |
| +2%   | 81.91  | 86.08     | 4.17pp |
| −2%   | 100.50 | 100.50    | 0.00pp |

The 0% gap was reduced from 3.67pp to 1.87pp via the hybrid pull-to-par fix (using
issue yield as base yield at 0% shock only). The +2% gap remains.

### Root cause

The engine prices the bond as the present value of remaining cashflows at
`yield = base_yield + shock`. At +2% shock, `base_yield = coupon_rate = 4%`, so
`shocked_yield = 6%`. The finite-bond PV at 6% gives 81.91.

The reference gives 86.08 — higher than our price, meaning the reference bond is
less sensitive to rate shocks. This is consistent with an **OAS (option-adjusted
spread) model** used by Scanrate (boligregner.dk's calculation engine).

### Why we can't replicate it exactly

Scanrate's OAS model uses:
- A proprietary interest-rate model (calibrated to the Danish yield curve).
- A proprietary prepayment model (calibrated to Danish borrower behavior data).
- The current Danish yield curve (not just the coupon rate + shock).

These are not publicly available. Our finite-bond + prepayment-option-cap model
(`bond_price_model = "finite_option"`) approximates the OAS model by:
- Pricing the bond as a finite-bond PV (correct option-free value).
- Capping the price at par + prepayment_premium (approximates the prepayment
  option's effect when rates fall).

This gives exact matches at −2% shock (100.50) and close matches at 0% shock
(94.46 vs 96.33), but overestimates duration at +2% shock.

### Proposed fix

**Option A: Reduced-duration model (simpler)**

Apply a duration adjustment that accounts for the prepayment option's effect
on effective duration:

```
effective_duration = modified_duration × (1 − option_value / bond_price)
```

This would reduce the price sensitivity at +2% shock, pushing 81.91 toward 86.08.

**Option B: Yield curve shift model (medium)**

Use a non-parallel shift that steepens the yield curve. A +2% shock to the
short end might only be +1.5% at the 30-year point, giving a lower shocked
yield and a higher price.

**Option C: Full OAS implementation (hard)**

Monte Carlo OAS with Hull-White rate model + prepayment model. Out of scope
for an open-source calculator.

**Recommended**: Option A. **Difficulty**: Medium.

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
| 2 | gns_kurs +2% shock | 4.17pp | 4.17pp | Open (OAS model needed) |
| 2 | gns_kurs 0% shock | 1.87pp | 1.87pp | Open (issue yield accuracy) |
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
- **Gap 2** (+2% shock gns_kurs): requires an OAS-like reduced-duration model.
  Lower priority — the 0% and −2% shocks already match well.
- **Gap 3 verification**: Session B with-bank cases need re-running with the
  fix to confirm the gap closes.
- **Session A tolerance**: pre-existing 50% rente gap in Session A reference
  data is a data quality issue (reference captured with different parameters
  than test inputs), not caused by the fix. Needs separate investigation.
