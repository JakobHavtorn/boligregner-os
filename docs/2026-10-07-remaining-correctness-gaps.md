# Remaining Correctness Gaps vs boligregner.dk

## Context

The engine now matches boligregner.dk closely on ydelse (0.0–0.4% off), ÅOP (0.02–0.08pp off),
afdrag (0.0–6.7% off), and restgæld (0.0–0.8% off) for single-component loans. Three gaps remain.
Each is documented below with root cause, evidence, and a proposed fix.

Reference data was captured from boligregner.dk on Oct 5–6, 2026 (Session A: single-component,
Session B: with-bank). See `local://boligregner-reference-data.md`.

---

## Gap 1: Flexlån horizon rente and ydelse_total (8–11% off)

### Symptoms

| Loan | Rente (0% shock) | Ydelse_h (0% shock) |
|------|-----------------|---------------------|
| F3   | 339.789 vs 382.697 (11.2% off) | 613.194 vs 665.983 (7.9% off) |
| F5   | 356.439 vs 393.498 (9.4% off) | 621.998 vs 678.002 (8.3% off) |

The 4% fixed loan matches well (rente 0.7% off, ydelse_h 0.4% off), so the gap is specific
to flexlån (F3/F5).

### Root cause

The horizon `rente_total` is after-tax: `(comp_interest + bidrag_total) × (1 − tax_rate)`.
The reference rente (382.697 for F3) sits between our after-tax value (339.789) and the
before-tax value (511.221). Two hypotheses were tested:

1. **Bidrag not tax-deductible** (rente = comp_interest × (1−tax) + bidrag_total):
   F3 → 380.423 (0.6% off, good), but 4% fixed → 440.402 (8.6% off, bad).
2. **Both tax-deductible** (rente = (comp_interest + bidrag) × (1−tax)):
   F3 → 339.789 (11.2% off, bad), but 4% fixed → 408.427 (0.7% off, good).

No single formula matches both flexlån and fixed. The reference likely computes flexlån
rente differently — possibly because flexlån interest resets every 3/5 years, and the
reference may use a different amortization schedule or tax treatment for the rate-reset
period vs the full 30-year term.

### Proposed fix

Investigate whether boligregner.dk computes flexlån horizon rente using:
- A different compounding frequency for the interest charge (e.g., monthly compounding
  on a quarterly-payment loan — interest accrues daily but payments are quarterly).
- A different tax deduction base (e.g., rentefradrag calculated on coupon only, not
  on the full annuity interest).
- An entirely different amortization model for flexlån (e.g., interest-only during
  the rate-reset period, then annuity on the remaining term).

**Approach**: Capture the F3 detail-page ydelsestabel (amortization schedule) from
boligregner.dk and compare period-by-period interest to our `_amortize` output. The
per-period breakdown will reveal where the reference diverges.

**Difficulty**: Medium — requires one more data capture session and per-period analysis.

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

### What is an OAS model?

**OAS** stands for **Option-Adjusted Spread**. It is a bond valuation method that
separates a bond's yield into two components:

1. **The option-free spread** — the extra yield a bond offers over the risk-free
   rate, ignoring any embedded options.
2. **The option cost** — the value of embedded options (like the borrower's right
   to prepay the mortgage).

Danish realkredit bonds have an embedded **call option**: the borrower can prepay
the mortgage at par (or par + a small premium) at any time. When interest rates rise,
this option is out-of-the-money (the borrower won't prepay), so the bond behaves
like a straight bond — its price drops. When rates fall, the option is
in-the-money (borrowers prepay), capping the bond's upside.

A standard finite-bond PV model (what we use) prices the bond as if the option
doesn't exist. An OAS model:

1. Projects many future interest-rate paths (using a stochastic model like
   Hull-White or Black-Karasinski).
2. For each path, simulates borrower prepayment behavior (a prepayment model).
3. Discounts the expected cashflows (option-adjusted) at the risk-free rate
   plus the OAS spread.
4. The OAS is the spread that makes the model price equal the observed market price.

The OAS model produces a **lower effective duration** than a straight bond because
the prepayment option limits price upside when rates fall. This explains why the
reference price (86.08) is higher than our finite-bond price (81.91) at +2% shock:
the OAS model's effective duration is lower, so the price drops less for a given
rate increase.

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

Instead of full OAS, apply a duration adjustment that accounts for the prepayment
option's effect on effective duration. The effective duration of a callable bond
is lower than the modified duration of a straight bond. A simple heuristic:

```
effective_duration = modified_duration × (1 − option_value / bond_price)
```

where `option_value` is estimated from the prepayment_premium and the probability
of the option being exercised. This would reduce the price sensitivity at +2%
shock, pushing 81.91 toward 86.08.

**Option B: Yield curve shift model (medium)**

Instead of a parallel yield shift (`coupon + shock`), use a non-parallel shift
that steepens the yield curve. Danish realkredit bonds are priced off the
swap curve, and rate shocks don't move all maturities equally. A +2% shock
to the short end might only be +1.5% at the 30-year point, giving a lower
shocked yield and a higher price.

**Option C: Full OAS implementation (hard)**

Implement a Monte Carlo OAS model with:
- A one-factor Hull-White interest-rate model calibrated to the Danish yield curve.
- A prepayment model based on the incentive function (savings from refinancing).
- 1000+ rate paths, discounted at the risk-free rate + OAS.

This would match Scanrate's values but requires yield-curve data, a prepayment
model, and significant computational effort. Probably out of scope for an
open-source calculator.

**Recommended**: Option A (reduced-duration model). It's the simplest approach
that addresses the root cause (overestimated duration) without requiring
proprietary data.

**Difficulty**: Medium (Option A) to Hard (Option C).

---

## Gap 3: With-bank ydelse (11–17% off)

### Symptoms

| Loan | Engine ydelse (monthly-equiv) | Reference | Gap |
|------|-------------------------------|-----------|-----|
| F3+bank   | 12.233 | 14.442 | 15.3% |
| F5+bank   | 13.004 | 14.676 | 11.4% |
| 4%+bank   | 12.935 | 15.666 | 17.4% |

Note: the raw `ydelse_before_tax` mixes quarterly realkredit payments with monthly
bank payments. The monthly-equivalent (`/3`) is an approximation because the bank
component (20% of provenu) pays monthly, not quarterly. ÅOP matches well
(0.04–0.46pp off) despite the ydelse gap.

### Root cause

The engine splits the desired provenu by `provenu_share` (80% realkredit, 20% bank),
then derives hovedstol for each component independently. This gives:

- Realkredit hovedstol: provenu × 0.80 / (price/100 − issue_costs)
- Bank hovedstol: provenu × 0.20 / (1 − 0) = provenu × 0.20

boligregner.dk uses a different split: it **caps the realkredit hovedstol at par**
(2.000.000 for a 2.500.000 provenu at 80% LTV), and the bank loan fills the
remaining gap. The bank hovedstol is larger than our 20% share because the
realkredit hovedstol is lower (capped at par, not derived from discounted price).

This means:
- Our realkredit hovedstol is too high (2.030.000 vs 2.000.000).
- Our bank hovedstol is too low (500.000 vs ~570.000).
- The bank loan has a higher rate (8.4%), so shifting more principal to the bank
  increases total ydelse — which is why our ydelse is lower than the reference.

### Proposed fix

Implement the **par-cap hovedstol model**:

1. Compute the realkredit hovedstol as `_hovedstol_for_provenu(provenu × share, price, issue_costs)`.
2. **Cap it at `provenu × share`** (the par value) — if the derived hovedstol
   exceeds the par amount, clamp it.
3. The bank hovedstol = `total_hovedstol_needed − realkredit_hovedstol_capped`.
4. The bank provenu share becomes residual, not a fixed percentage.

This requires changing how `_compute_component` derives the bank hovedstol.
Currently it uses `provenu_share` directly; it needs to use the residual after
the realkredit par cap.

**Implementation**:
- Add a `par_cap: bool = True` field to `LoanSpec` (default True for realkredit,
  False for bank) or to `FinancingAlternative`.
- In `calculate()`, after computing realkredit hovedstol, cap it and recompute
  the bank component's provenu share as the residual.
- This is a `calculate()`-level change, not an engine-math change — the
  `calculate()` interface signature stays the same.

**Difficulty**: Medium — the math is straightforward but the provenu-share
reallocation touches the `calculate()` orchestration and may affect
existing tests that assert specific hovedstol values.

---

## Summary

| Gap | Metric | Current deviation | Root cause | Fix difficulty |
|-----|--------|-------------------|------------|----------------|
| 1 | Flexlån rente/ydelse_h | 8–11% | Different interest/tax model for flexlån | Medium |
| 2 | gns_kurs +2% shock | 4.17pp | OAS model (proprietary) | Medium (reduced-duration) |
| 3 | With-bank ydelse | 11–17% | Hovedstol par cap not implemented | Medium |

All three require either additional reference data (Gap 1), a more
sophisticated bond-pricing model (Gap 2), or a hovedstol-allocation change
(Gap 3). None can be fixed by tuning existing parameters.
