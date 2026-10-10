# Kursværdi Discrepancy Investigation (Limitation 5)

## Summary

**Root cause**: The engine computes `kursværdi = round_up_1000(hovedstol) × price / 100`
(from the **rounded** hovedstol). boligregner.dk computes `kursværdi` from the
**unrounded** hovedstol, which equals exactly `provenu + issue_costs_nominal`.

The rounding-up of hovedstol to the nearest 1000 inflates the kursværdi by
`rounding_uplift × price / 100`. This also inflates the kontant (net cash)
beyond the desired provenu.

No new model fields are needed. The fix is to compute kursværdi from the
unrounded hovedstol, not the rounded one.

## The engine's computation path

For obligationslån (FIXED), in `_hovedstol_for_provenu` (engine.py:495-512):

```python
# Nominal mode (issue_costs_nominal set):
raw = (provenu + issue_costs_nominal) / (price / 100)
hovedstol = round_up_1000(raw)  # ceil to nearest 1000
```

Then in `_compute_component` (engine.py:644-645):

```python
kursvaerdi = hovedstol * spec.price / _HUNDRED  # uses ROUNDED hovedstol
```

The algebraic identity makes the unrounded kursværdi exact:

```
raw_hovedstol = (provenu + issue_nominal) / (price/100)
unrounded_kursvaerdi = raw_hovedstol × price/100
                    = (provenu + issue_nominal) / (price/100) × price/100
                    = provenu + issue_nominal
```

So the unrounded kursværdi = `provenu + issue_costs_nominal` **exactly**.
This means `kontant = kursværdi - udst.omk. = (provenu + issue_nominal) - issue_nominal = provenu` exactly.

The engine's rounded kursværdi exceeds this by:

```
delta = (rounded_hovedstol - raw_hovedstol) × price / 100
      = rounding_uplift × price / 100
```

## Per-case deltas

| Case | Price | Provenu | Issue Nom. | Raw Hovedstol | Rounded Hovedstol | Engine Kursværdi | Unrounded Kursværdi (=prov+nom) | Delta |
|------|-------|---------|------------|---------------|-------------------|------------------|---------------------------------|-------|
| fast5_20 | 99.52 | 2,658,348 | 12,166 | 2,683,394.29 | 2,684,000 | 2,671,116.80 | 2,670,514 | **602.80** |
| fast5_21 | 98.52 | 2,658,891 | 12,167 | 2,711,183.52 | 2,712,000 | 2,671,862.40 | 2,671,058 | **804.40** |
| fast5_22 | 98.52 | 2,658,891 | 12,167 | 2,711,183.52 | 2,712,000 | 2,671,862.40 | 2,671,058 | **804.40** |
| fast5_23 | 98.52 | 2,658,891 | 12,167 | 2,711,183.52 | 2,712,000 | 2,671,862.40 | 2,671,058 | **804.40** |
| fast5_25 | 98.83 | 2,658,530 | 12,166 | 2,702,313.06 | 2,703,000 | 2,671,374.90 | 2,670,696 | **678.90** |
| fast5_30 | 98.83 | 2,658,530 | 12,166 | 2,702,313.06 | 2,703,000 | 2,671,374.90 | 2,670,696 | **678.90** |

## Hypotheses tested

### H1: Rounding of hovedstol (CONFIRMED — root cause)

The engine rounds hovedstol up to the nearest 1000 before computing
kursværdi. boligregner.dk computes kursværdi from the unrounded hovedstol.

- Unrounded kursværdi = `provenu + issue_costs_nominal` (exactly, by algebraic identity)
- Engine kursværdi = `rounded_hovedstol × price / 100` (larger by the rounding uplift)
- Delta = `rounding_uplift × price / 100`
- Verified: the delta matches `rounding_uplift × price / 100` exactly for all 6 cases

### H2: Prisskæring (price haircut) — REJECTED

boligregner.dk's help text says kursværdi = obligationshovedstol × (kurs +
prisskæring) / 100. If prisskæring were nonzero, it would explain the
difference. But the implied prisskæring values are -0.022 to -0.030 —
negative and tiny (a few basis points). This is not a real prisskæring;
it's an artifact of the rounding. Prisskæring is typically 0 for standard
loans and would be a positive value if applied.

### H3: Kurtage (broker fee) deducted from kursværdi — REJECTED

The reference data docs show that udst.omk. is itemized:
`udst.omk. = ekspeditionsgebyr + kurtage + fast tinglysning + procentuel
tinglysning`. The kurtage is already included in the `issue_costs_nominal`
total (12,166/12,167 kr). It is NOT deducted separately from kursværdi.
The "kurtage needed to match" (602.80–804.40 kr) is too small to be a
real kurtage (real kurtage is ~4,000 kr per the reference data) and is
exactly the rounding uplift, confirming it's a rounding artifact.

### H4: kursværdi = provenu + issue_nominal (equivalent to H1) — CONFIRMED

If boligregner.dk sets kontant = provenu exactly, then kursværdi = provenu
+ issue_nominal. This is mathematically identical to H1 (unrounded
hovedstol × price/100 = provenu + issue_nominal). The engine's kontant
exceeds provenu by the same delta (602.80–804.40 kr).

## Root cause conclusion

**The engine computes kursværdi from the rounded hovedstol. boligregner.dk
computes it from the unrounded hovedstol.**

The unrounded hovedstol produces kursværdi = `provenu + issue_costs_nominal`
exactly, making kontant = provenu exactly. The rounded hovedstol inflates
both kursværdi and kontant by `rounding_uplift × price / 100`.

This is a **computation-order bug**, not a missing-field problem. The
hovedstol must still be rounded to the nearest 1000 for amortization
purposes (the loan principal is a whole number of thousands). But
kursværdi should be computed from the unrounded hovedstol, or equivalently
set to `provenu + issue_costs_nominal`.

## Recommended fix

In `_compute_component` (engine.py:644-645), change:

```python
# CURRENT (line 645):
kursvaerdi = hovedstol * spec.price / _HUNDRED
```

To compute from the unrounded hovedstol. This requires `_hovedstol_for_provenu`
to return the raw (unrounded) hovedstol alongside the rounded one, or
equivalently, set kursværdi = `component_provenu + udstedelse`:

```python
# FIX:
kursvaerdi = component_provenu + udstedelse
```

where `udstedelse` is the udstedelsesomkostning (already computed at line
646-649). This makes kontant = `kursværdi - udstedelse = component_provenu`
exactly, matching boligregner.dk.

No new model fields are needed. The fix is a one-line change in the
kursværdi computation, using values already available in `_compute_component`.

## Verification

The throwaway script `kursvaerdi_investigation.py` computes all 6 Fast 5%
cases and verifies that:
1. `unrounded_kursvaerdi = provenu + issue_costs_nominal` (exact)
2. `delta = (rounded - raw) × price / 100` (exact for all cases)
3. `engine_kontant - provenu = delta` (the excess cash from rounding)

Run: `uv run python kursvaerdi_investigation.py`
