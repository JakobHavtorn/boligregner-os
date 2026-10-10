"""FastAPI HTTP server for boligregner-os.

Exposes the calculation engine over HTTP and serves a single-page
HTML results renderer (matching boligregner.dk's URL structure).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .engine import PRESETS, amortization_schedule, calculate
from .market_data import (
    get_bidragssatser,
    get_bond_prices,
    get_market_rates,
    get_nominal_rate,
    get_reference_rate,
    refresh_market_rates,
)
from .models import AmortizationSchedule, CalculatorInput, CalculatorResult, LoanType

app = FastAPI(
    title="boligregner-os",
    description="Open-source Danish realkredit mortgage calculator",
    version="0.1.0",
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


@app.post("/api/calculate", response_model=CalculatorResult)
def api_calculate(input: CalculatorInput) -> CalculatorResult:
    """Accept a CalculatorInput body and return the computed CalculatorResult."""
    try:
        return calculate(input)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.post("/api/amortization/{alt_index}", response_model=AmortizationSchedule)
def api_amortization(alt_index: int, input: CalculatorInput) -> AmortizationSchedule:
    """Return the year-by-year amortization schedule for one alternative."""
    if alt_index < 0 or alt_index >= len(input.alternatives):
        raise HTTPException(
            status_code=404, detail=f"Alternative index {alt_index} out of range"
        )
    try:
        return amortization_schedule(input, alt_index)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/api/presets")
def api_presets() -> dict:
    """Return all presets as a dictionary of CalculatorInput JSON objects."""
    return {
        "presets": {
            name: preset.model_dump(mode="json") for name, preset in PRESETS.items()
        }
    }


@app.get("/api/presets/{name}")
def api_preset(name: str) -> dict:
    """Return a single named preset as CalculatorInput JSON."""
    if name not in PRESETS:
        raise HTTPException(status_code=404, detail=f"Unknown preset '{name}'")
    return PRESETS[name].model_dump(mode="json")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Serve the HTML results page."""
    return templates.TemplateResponse(request, "results.html")


@app.get("/resultater/beregning", response_class=HTMLResponse)
def resultater_beregning(request: Request) -> HTMLResponse:
    """Serve the same HTML page (boligregner.dk URL structure)."""
    return templates.TemplateResponse(request, "results.html")


@app.get("/resultater/alternativ/{alt_index}", response_class=HTMLResponse)
def resultater_alternativ(request: Request, alt_index: int) -> HTMLResponse:
    """Serve the per-alternative subpage (boligregner.dk URL structure)."""
    return templates.TemplateResponse(
        request, "alternative.html", {"alt_index": alt_index}
    )


@app.get("/api/market-rates/{loan_type}")
def api_market_rates(loan_type: LoanType) -> dict:
    """Current nominal rate for a specific loan type (F1, F3, F5, FIXED).

    Returns {"loan_type": "f3", "rate": "0.0239", "fetched_at": ...}.
    FIXED returns rate=None (use /api/bond-prices for fixed-rate data).
    """
    rate = get_nominal_rate(loan_type)
    rates = get_market_rates()
    return {
        "loan_type": loan_type.value,
        "rate": str(rate) if rate is not None else None,
        "fetched_at": rates.fetched_at.isoformat(),
    }


@app.get("/api/bidragssatser")
def api_bidragssatser(
    institute: str | None = None, loan_type: str | None = None
) -> dict:
    """Bidragssatser by institute x LTV x loan type x afdragsfrihed.

    Returns flat list of BidragssatsEntry dicts, optionally filtered.
    """
    entries = get_bidragssatser(institute=institute, loan_type=loan_type)
    return {
        "bidragssatser": [e.model_dump(mode="json") for e in entries],
    }


@app.get("/api/bond-prices")
def api_bond_prices() -> dict:
    """Current fixed-rate bond prices (kurs). Flexlan always 100 (par)."""
    return get_bond_prices()


@app.get("/api/reference-rates/{rate_type}")
def api_reference_rates(rate_type: str) -> dict:
    """Reference rate (CIBOR 3M/6M, CITA 3M, DESTR). Cached per source."""
    rate = get_reference_rate(rate_type)
    return {
        "rate_type": rate_type,
        "rate": str(rate) if rate is not None else None,
    }


@app.post("/api/market-rates/refresh")
def api_market_rates_refresh() -> dict:
    """Force refresh all sources. Returns fresh snapshot."""
    rates = refresh_market_rates()
    return rates.model_dump(mode="json")
