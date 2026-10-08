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

## Implemented framework structure

The parameterized test framework lives in `tests/test_engine.py` and extends
the existing `TestReferenceComparison` class. No parallel framework was created.

### REFERENCE_CASES

All reference data is stored in a single `REFERENCE_CASES` list of dicts. Each
dict has these keys:

| Key | Type | Description |
|-----|------|-------------|
| `id` | `str` | Unique identifier, used as the pytest test ID |
| `input` | `CalculatorInput` | Fully-configured engine input for this session |
| `component_ppys` | `list[int]` | Payments-per-year for each component (e.g. `[4]` or `[4, 12]`) |
| `aap_ppy` | `int` | Periods per year for ÅOP effective-rate conversion |
| `expected` | `dict` | Reference values to assert against (see fields below) |
| `tol` | `dict` | Per-metric tolerances (see tolerances below) |
| `skip` | `str` | (Optional) Skip reason for placeholder cases |

### Expected fields

The `expected` dict can contain any subset of these fields. Tests skip
gracefully when a field is absent.

**Top-level metrics:**

| Field | Type | Description |
|-------|------|-------------|
| `hovedstol` | `Decimal` | Total hovedstol (obligationshovedstol) |
| `ydelse` | `Decimal` | Monthly-equivalent ydelse before tax |
| `aap` | `Decimal` | Effective annual ÅOP (nominal converted to effective) |

**Horizon 0% shock (`expected["horizon_0pct"]`):**

| Field | Type | ScenarioRow attribute | Description |
|-------|------|----------------------|-------------|
| `rente` | `Decimal` | `rente_total` | Total interest+bidrag over horizon, after tax |
| `afdrag` | `Decimal` | `afdrag_total` | Total principal paid over horizon |
| `ydelse` | `Decimal` | `ydelse_total` | rente_total + afdrag_total |
| `restgaeld` | `Decimal` | `restgaeld` | Remaining debt at horizon end |
| `ydelse_start` | `Decimal` | `ydelse_start` | Monthly payment after tax at period start |
| `ydelse_slut` | `Decimal` | `ydelse_slut` | Monthly payment after tax at horizon end |
| `indfrielse` | `Decimal` | `indfrielse` | Total payoff amount incl. costs at horizon |
| `periodeomkostning` | `Decimal` | `periodeomkostning` | ydelse_total + indfrielse − provenu (after tax) |

**Horizon gns_kurs (`expected["horizon_gns_kurs"]`):**

A dict mapping `Decimal` shock values to expected gns_kurs. Typically:

```python
"horizon_gns_kurs": {
    Decimal(0): Decimal("95.28"),
    Decimal("0.02"): Decimal("86.08"),
    Decimal("-0.02"): Decimal("100.50"),
}
```

**Shocked horizons (`expected["horizon_+2pct"]`, `expected["horizon_-2pct"]`):**

Same structure as `horizon_0pct` — each can contain `rente`, `afdrag`,
`ydelse`, `restgaeld`, and `indfrielse` references.

### Tolerances

Tolerances are stored per-case in the `tol` dict and are configurable per-metric.
The framework uses both relative and absolute tolerances depending on the metric:

| Key | Type | Description |
|-----|------|-------------|
| `hovedstol` | `Decimal \| None` | `None` = exact match; `Decimal("0.01")` = 1% relative |
| `ydelse` | `Decimal` | Relative tolerance (e.g. `0.05` = 5%) |
| `aap` | `Decimal` | Absolute tolerance in fraction (e.g. `0.005` = 0.5pp) |
| `horizon_rente` | `Decimal` | Relative tolerance for 0% shock rente |
| `horizon_afdrag` | `Decimal` | Relative tolerance for 0% shock afdrag |
| `horizon_ydelse` | `Decimal` | Relative tolerance for 0% shock ydelse |
| `horizon_restgaeld` | `Decimal` | Relative tolerance for 0% shock restgaeld |
| `horizon_gns_kurs` | `Decimal` | Absolute tolerance in pp (e.g. `5` = 5pp) |
| `horizon_ydelse_start` | `Decimal` | Relative tolerance for ydelse_start |
| `horizon_ydelse_slut` | `Decimal` | Relative tolerance for ydelse_slut |
| `horizon_indfrielse` | `Decimal` | Relative tolerance for indfrielse |
| `horizon_periodeomkostning` | `Decimal` | Relative tolerance for periodeomkostning |
| `horizon_{metric}_shocked` | `Decimal` | Relative tolerance for shocked scenarios |

### Test methods

The `TestReferenceComparison` class has these parametrized test methods. Each
is parametrized over all `REFERENCE_CASES` with `ids` from the `id` field:

- `test_hovedstol` — exact or relative tolerance
- `test_ydelse` — monthly-equivalent ydelse
- `test_aap` — effective annual ÅOP
- `test_horizon_rente` — 0% shock rente_total
- `test_horizon_afdrag` — 0% shock afdrag_total
- `test_horizon_ydelse` — 0% shock ydelse_total
- `test_horizon_restgaeld` — 0% shock restgaeld
- `test_horizon_gns_kurs` — gns_kurs at all shocks
- `test_horizon_ydelse_start` — 0% shock ydelse_start
- `test_horizon_ydelse_slut` — 0% shock ydelse_slut
- `test_horizon_indfrielse` — 0% shock indfrielse
- `test_horizon_periodeomkostning` — 0% shock periodeomkostning
- `test_horizon_shocked` — +2% and -2% shock metrics

### Skip mechanism

Placeholder cases use a `"skip"` key with a reason string. The
`_ref_case_params()` helper wraps each case in a `pytest.param` with
`pytest.mark.skip(reason=...)` when the `skip` key is present. This ensures
all parametrized tests for that case are skipped at collection time with
a clear reason.

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

## How to add a new reference case

Once reference data is captured from boligregner.dk:

1. **Append a dict to `REFERENCE_CASES`** in `tests/test_engine.py`
2. **Set the `id`** to a descriptive string (used as the pytest test ID)
3. **Build the `input`** — a `CalculatorInput` matching the captured session
4. **Fill in `expected`** with the captured reference values
5. **Set `tol`** with appropriate per-metric tolerances
6. **Remove the `skip` key** (if upgrading a placeholder to a real case)

### Example: adding a captured provenu 1.500.000 case

```python
{
    "id": "f3_provenu_1500000",
    "input": CalculatorInput(
        desired_provenu=Decimal(1500000),
        start_date=date(2026, 10, 6),
        horizon_years=5,
        tax_rate=Decimal("0.336"),
        alternatives=[
            FinancingAlternative(
                label="F3",
                components=[
                    LoanSpec(
                        component=LoanComponent.REALKREDIT,
                        loan_type=LoanType.F3,
                        rate=Decimal("0.0323"),
                        price=Decimal(100),
                        maturity_years=30,
                        issue_costs_pct=Decimal("0.017781"),
                        bidragssats=Decimal("0.0095"),
                        provenu_share=Decimal(1),
                    ),
                ],
            ),
        ],
    ),
    "component_ppys": [4],
    "aap_ppy": 4,
    "expected": {
        "hovedstol": Decimal(1528000),  # from boligregner.dk
        "ydelse": Decimal(7819),  # monthly-equivalent
        "aap": Decimal("0.0448"),  # effective annual
        "horizon_0pct": {
            "rente": Decimal(229618),
            "afdrag": Decimal(169972),
            "ydelse": Decimal(399590),
            "restgaeld": Decimal(1358028),
        },
    },
    "tol": {
        "hovedstol": None,  # exact
        "ydelse": Decimal("0.05"),
        "aap": Decimal("0.005"),
        "horizon_rente": Decimal("0.115"),
        "horizon_afdrag": Decimal("0.20"),
        "horizon_ydelse": Decimal("0.20"),
        "horizon_restgaeld": Decimal("0.05"),
        "horizon_gns_kurs": Decimal(5),
        "horizon_ydelse_start": Decimal("0.05"),
        "horizon_ydelse_slut": Decimal("0.05"),
        "horizon_indfrielse": Decimal("0.10"),
        "horizon_periodeomkostning": Decimal("0.10"),
        "horizon_rente_shocked": Decimal("0.20"),
        "horizon_afdrag_shocked": Decimal("0.20"),
        "horizon_ydelse_shocked": Decimal("0.20"),
        "horizon_restgaeld_shocked": Decimal("0.10"),
    },
}
```

### Upgrading a placeholder

When reference data becomes available for a placeholder case:

1. Replace the `"skip"` key's fake values (`999999`, `0.9999`) with real data
2. Remove the `"skip"` key entirely
3. Update the `id` to remove the `placeholder_` prefix (optional but recommended)
4. Run `uv run pytest tests/test_engine.py -q -k "<case_id>"` to verify

## Current placeholder cases

The following placeholder cases are in `REFERENCE_CASES` with
`pytest.mark.skip(reason='reference data not yet captured from boligregner.dk')`:

| Case ID | Variant | Notes |
|---------|---------|-------|
| `placeholder_provenu_1500000` | Provenu 1.500.000 | F3 single-component |
| `placeholder_provenu_3500000` | Provenu 3.500.000 | F3 single-component |
| `placeholder_ltv_60_40` | LTV split 60/40 | F3 + bank |
| `placeholder_ltv_95_5` | LTV split 95/5 | F3 + bank |
| `placeholder_afdragsfri_5yr` | Afdragsfrihed 5yr IO | F3 with interest_only_years=5 |
| `placeholder_maturity_20yr` | 20-year maturity | F3 with maturity_years=20 |
| `placeholder_maturity_10yr` | 10-year maturity | F3 with maturity_years=10 |
| `placeholder_f1_flexlaan` | F1 flexlån | F1 loan type (1-year rate reset) |
| `placeholder_tax_rate_25pct` | Different tax rate | 25% tax rate (vs 33.6%) |

All placeholders use clearly-fake expected values (`Decimal(999999)`,
`Decimal("0.9999")`) and are skipped at collection time. No placeholder
values look like real reference data.
