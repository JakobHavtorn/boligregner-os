# AGENTS.md

Guide for AI agents working on this repository.

## Project overview

boligregner-os is an open-source Danish realkredit (mortgage) calculator. It compares financing alternatives (F3/F5 flexlån, fixed-rate obligations, T-lån, afdragsfrihed) with 5-year horizon analysis under configurable rate-shock scenarios.

## Architecture

**Deep module design.** The engine (`engine.py`) is the single source of truth: `calculate(CalculatorInput) -> CalculatorResult` and `amortization_schedule(input, alt_index) -> AmortizationSchedule`. Everything else is a thin adapter:

- `models.py` — Pydantic schemas (LoanSpec, CalculatorInput, CalculatorResult, etc.)
- `engine.py` — All mortgage math: hovedstol derivation, annuity amortization, ÅOP via IRR, horizon analysis, rate shocks, afdragsfrihed, T-lån
- `server.py` — FastAPI adapter: POST /api/calculate, POST /api/amortization/{n}, GET /api/presets, page routes
- `templates/results.html` — Single-page frontend (inline CSS/JS, no build step)
- `templates/alternative.html` — Per-alternative subpage with ydelsestabel and CSV export
- `mcp_server.py` / `mcp_cli.py` — MCP tool adapter for AI agents
- `tests/test_engine.py` — Engine tests (32 tests)

## Key constraints

### Monetary values
All monetary values in the engine are `Decimal`. Never use `float` for money. The `decimal` module's `ln()` and `ROUND_CEILING` are used for precision-critical calculations like `_solve_for_n()`.

### Backward compatibility
New fields on Pydantic models must default to preserve existing behavior. Existing tests must pass unchanged. Existing presets must produce identical results.

### No external frontend dependencies
Templates are self-contained HTML with inline CSS/JS. No CDN fonts, no npm, no JS libraries. The only external resource is the system font stack.

### Danish UI
All user-facing text is in Danish. Number formatting uses `da-DK` locale. API endpoint names are in English; page routes match boligregner.dk's Danish URL structure (`/resultater/beregning`, `/resultater/alternativ/{n}`).

### XSS prevention
All dynamic content inserted into HTML must go through `escapeHtml()`. This includes labels, dates, and numeric values in attribute contexts.

## Loan types

- `FIXED` — Fixed-rate obligation (fast rente). Rate shocks affect bond redemption price, not amortization.
- `F3` / `F5` / `F1` — Rentetilpasningslån (flexlån) with 3/5/1-year rate adjustment. Rate shocks affect amortization rate, redemption at par.
- `T` — T-lån: fixed monthly ydelse, variable duration. Rate shocks change duration, not payment. Negative amortization when payment < interest (balance grows).
- `CITA` — CITA-referencerente: short-period variable rate loan. Rate = reference_rate + margin, resets every `reset_months` months.
- `CIBOR` — CIBOR-referencerente: same structure as CITA, being phased out in favor of DESTR.
- `DESTR` — Compounded overnight rate replacing CIBOR. Uses daily compounding converted to monthly equivalent.

## Special features

- **Afdragsfrihed** (`interest_only_years`): Interest-only period at loan start. Principal=0 during IO, then annuity over remaining term. `ydelse_before_tax` reports the post-IO annuity payment.
- **T-lån** (`fixed_ydelse`): `_solve_for_n()` computes duration from fixed payment. If payment doesn't cover interest, balance grows (negative amortization).
- **CITA/CIBOR/DESTR** (`reference_rate`, `margin`, `reset_months`): Rate = reference_rate + margin + bidragssats. Rate shocks apply to the reference rate, not the margin. Payment is re-annuitized at each reset boundary over the remaining term. DESTR uses daily compounding via `_daily_to_monthly()`.

## Testing

```bash
uv run pytest tests/ -q
```

Tests verify annuity math, IRR, hovedstol derivation, ÅOP ordering, horizon scenarios, rate shock effects, fixed-obligation price sensitivity, afdragsfrihed, T-lån, CITA/CIBOR/DESTR rate paths, and preset smoke tests.

## Running the server

```bash
uv sync --extra dev --reinstall  # non-editable install (fixes Python 3.14 .pth issue)
uv run boligregner --port 8000
```

If `uv run boligregner` fails with ModuleNotFoundError, use:
```bash
PYTHONPATH=src uv run python -m boligregner.cli --port 8000
```

## Package management

This project uses `uv` with the `uv_build` backend (not hatchling/pip). The `pyproject.toml` has `[tool.uv.build-backend]` with `module-name = "boligregner"` and `module-root = "src"`.

## What NOT to do

- Do not add external dependencies to the frontend (no CDN, no npm)
- Do not use `float` for monetary calculations in the engine
- Do not change the `calculate()` interface signature
- Do not modify server.py templates path logic (uses `Path(__file__).parent`)
- Do not add mentions of Vitec Scanrate A/S (removed by request)
- Do not use `text-transform: uppercase` on labels (sentence case only)
- Do not hardcode alternative count or color count — both are dynamic
