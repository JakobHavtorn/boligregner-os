# Test Session Variants for boligregner.dk Reference Data

## Context

The current reference test set is narrow — one provenu (2.500.000), one start date,
one LTV split (80/20), no afdragsfrihed, one maturity (30yr). Capturing additional
boligregner.dk sessions broadens coverage and validates the engine across more
configurations.

## Priority variants

| Session variant | What it tests | Priority | Expected reference values to capture |
|-----------------|---------------|----------|--------------------------------------|
| Different provenu (1.500.000) | Hovedstol derivation at smaller scale | High | ydelse, ÅOP, hovedstol, horizon (rente, afdrag, ydelse_h, restgæld, gns_kurs) |
| Different provenu (3.500.000) | Hovedstol derivation at larger scale | High | Same as above |
| Different LTV split (60/40) | Bank split model at different ratio | High | ydelse, ÅOP, hovedstol per component |
| Different LTV split (95/5) | Minimal bank component | Medium | ydelse, ÅOP |
| Afdragsfrihed (5yr IO) | Interest-only period + post-IO annuity | Medium | ydelse (during IO and post-IO), ÅOP, horizon |
| 20-year maturity | Amortization over shorter term | Medium | ydelse, ÅOP, horizon |
| 10-year maturity | Short-term amortization | Low | ydelse, ÅOP |
| F1 flexlån (if available) | 1-year rate reset | Low | ydelse, ÅOP, horizon |
| Different tax rate | Tax deduction model | Low | ydelse after tax, horizon rente |

## How to capture

1. Go to [boligregner.dk/beregn](https://www.boligregner.dk/beregn)
2. Enter the variant's parameters (provenu, LTV, loan type, etc.)
3. From the comparison table, capture: ydelse (f.s.), ÅOP (f.s.), hovedstol
4. Click into each alternative to capture the horizon table (rente, afdrag,
   ydelse, restgæld, gns_kurs, indfrielse) at 0%, +2%, -2% rate shocks
5. Note the date — reference values change as market rates update

## What to record per session

```
- Date: YYYY-MM-DD
- Provenu: NNNNNNN
- Start date: YYYY-MM-DD
- Tax rate: 0.XXX
- Horizon years: 5
- For each alternative:
  - Label (e.g., "30 år F3")
  - Loan type, rate, price, bidragssats, issue_costs_pct
  - Hovedstol
  - Ydelse (f.s.)
  - ÅOP (f.s.)
  - For each rate shock (-2%, 0%, +2%):
    - Rente, afdrag, ydelse, restgæld, gns_kurs, indfrielse
```

## Adding captured sessions to the test framework

Once the parameterized test framework is implemented, adding a new session is
just appending a dict to `REFERENCE_CASES` in `tests/test_engine.py`:

```python
REFERENCE_CASES = [
    # ... existing cases ...
    {
        "label": "F3-provenu-1500000",
        "input": CalculatorInput(
            desired_provenu=Decimal(1500000),
            # ... other fields matching the captured session ...
        ),
        "component_ppys": [4],  # single realkredit, quarterly
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(XXX000),
            "ydelse": Decimal(XXXX),
            "aap": Decimal("X.XX"),
            "rente_0pct": Decimal(XXXXXX),
            # ... etc ...
        },
        "tolerances": {
            "ydelse": Decimal("0.05"),
            "aap": Decimal("0.005"),
            # ... etc ...
        },
    },
]
```
