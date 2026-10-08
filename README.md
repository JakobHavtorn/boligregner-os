# boligregner-os

An open-source Danish realkredit mortgage calculator with financing-alternative comparison and 5-year horizon analysis. An independent reimplementation of Danish mortgage calculation logic, not affiliated with or endorsed by any commercial provider.

## What it does

Given a desired net cash amount (ønsket provenu) and a set of financing alternatives (e.g. F3 flexlån, F5 flexlån, 4% fixed-rate obligation, each combined with a bank loan), the calculator:

1. **Derives hovedstol** — inverts the obligation price and issue costs to determine the gross loan principal needed to hit the desired net cash.
2. **Computes ydelse** — level monthly payment (annuity) including coupon rate + bidragssats (administration fee).
3. **Computes ÅOP** — annual percentage rate of charge via IRR on the actual cash-flow stream (accounts for discount obligations correctly).
4. **5-year horizon analysis** — amortizes each loan for the horizon period, applies rate shocks (−2%, 0%, +2%), and computes:
   - Total interest (after tax deduction at the Danish rentefradrag rate)
   - Total principal paid
   - Remaining debt (restgæld)
   - Redemption payoff (indfrielse)
   - Periodeomkostning — total cost of borrowing over the period vs. paying cash
5. **Tax deduction** — applies the marginal tax rate (default 33.6%) to the interest portion.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Interface: calculate(CalculatorInput) →     │
│              CalculatorResult               │
├─────────────────────────────────────────────┤
│  hovedstol derivation · annuity math ·       │
│  ÅOP IRR · 5yr horizon · -2/0/+2% scenarios │
│  bond pricing · tax deduction · presets      │
└─────────────────────────────────────────────┘
         │           │            │
    FastAPI       HTML page    MCP tool
    (adapter)     (adapter)    (adapter)
```

The calculation engine (`engine.py`) is a **deep module**: one function, two types, all the Danish mortgage math hidden behind it. The FastAPI server, HTML frontend, and MCP tool server are thin adapters at that seam.

## Installation
```bash
git clone <repo-url>
cd boligregner-os
make install
```

This installs dependencies via `uv sync --extra dev` and sets up pre-commit hooks.
Alternatively: `uv sync --extra dev && pre-commit install`.

## Usage

### As a library

```python
from decimal import Decimal
from datetime import date
from boligregner import calculate, PRESETS
from boligregner.models import (
    CalculatorInput,
    FinancingAlternative,
    LoanSpec,
    LoanType,
    LoanComponent,
)

# Use the default preset (matches boligregner.dk sample)
result = calculate(PRESETS["default"])

# Or build your own input
custom = CalculatorInput(
    desired_provenu=Decimal("3000000"),
    start_date=date(2026, 10, 4),
    horizon_years=5,
    tax_rate=Decimal("0.336"),
    alternatives=[
        FinancingAlternative(
            label="30 år F3, 30 år Banklån",
            components=[
                LoanSpec(
                    component=LoanComponent.REALKREDIT,
                    loan_type=LoanType.F3,
                    rate=Decimal("0.035"),
                    price=Decimal("100"),
                    maturity_years=30,
                    issue_costs_pct=Decimal("0.0177"),
                    bidragssats=Decimal("0.006"),
                    provenu_share=Decimal("0.80"),
                ),
                LoanSpec(
                    component=LoanComponent.BANK,
                    loan_type=LoanType.F1,
                    rate=Decimal("0.045"),
                    price=Decimal("100"),
                    maturity_years=30,
                    provenu_share=Decimal("0.20"),
                ),
            ],
        ),
    ],
)
result = calculate(custom)

# Access results
for alt in result.alternatives:
    print(f"{alt.label}: hovedstol={alt.total_hovedstol}, ÅOP={alt.aap_before_tax:.2%}")
```

### As a web server

```bash
uv run boligregner  # starts on http://127.0.0.1:8000
```

Then open `http://127.0.0.1:8000/` or `http://127.0.0.1:8000/resultater/beregning` for the results page.

#### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/calculate` | Accepts a `CalculatorInput` JSON body, returns `CalculatorResult` JSON |
| `GET` | `/api/presets` | Returns all available presets |
| `GET` | `/api/presets/{name}` | Returns a named preset as `CalculatorInput` JSON |
| `POST` | `/api/amortization/{alt_index}` | Returns yearly amortization schedule for alternative at index |

Example:
```bash
curl -X POST http://127.0.0.1:8000/api/calculate \
  -H "Content-Type: application/json" \
  -d "$(curl -s http://127.0.0.1:8000/api/presets/default)"
```

### As an MCP tool

The calculator is also exposed as an MCP (Model Context Protocol) tool for AI agents:

```bash
uv run python -m boligregner.mcp_cli
```

This starts an MCP server on stdio exposing a `calculate_mortgage` tool that accepts either a preset name or full input parameters. Agents can use it to compute mortgage comparisons programmatically.

### Running tests

```bash
uv run pytest tests/ -v
```

## Loan types

| Type | Description |
|------|-------------|
| `FIXED` | Fast rente obligationslån (fixed-rate bond loan, e.g. 4% obligation) |
| `F1` | Rentetilpasningslån with 1-year rate adjustment |
| `F3` | Rentetilpasningslån with 3-year rate adjustment |
| `F5` | Rentetilpasningslån with 5-year rate adjustment |

## Key concepts

- **Provenu**: The net cash the borrower wants to receive after all costs.
- **Hovedstol**: The gross loan principal. For discount obligations, this is higher than the provenu because the bond trades below par.
- **Kursværdi**: Hovedstol × price/100 — the market value of the loan.
- **Udstedelsesomkostninger**: Issue costs as a fraction of hovedstol.
- **Bidragssats**: Annual administration fee added to the coupon rate for payment calculation.
- **ÅOP**: Årlige omkostninger i procent — the effective annual cost rate including all fees.
- **Periodeomkostning**: Total cost of borrowing over the horizon period (ydelse + indfrielse − provenu), after tax.
- **Indfrielse**: Redemption payoff at the horizon date.
- **Rentefradrag**: Tax deduction on interest payments (default 33.6% marginal tax rate).

## License

MIT

## Disclaimer

This is an independent open-source project. The calculations are approximate and intended for educational/analysis purposes. The author assumes no responsibility for financial decisions made based on this tool. For professional mortgage advice, consult a licensed financial advisor.
