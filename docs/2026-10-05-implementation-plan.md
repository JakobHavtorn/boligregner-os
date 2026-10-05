# Implementation Plan: Live Market Data Integration

## Overview

Replace hardcoded values in `engine.py` PRESETS with live-sourced market data. Add a data layer
(fetchers + cache), new API endpoints, and MCP tools. The engine's `calculate()` interface stays
unchanged — the data layer populates `LoanSpec` fields before calculate is called.

## Architecture

```mermaid
classDiagram
    direction TB

    class server_py {
        +GET /api/market-rates/{loan_type}
        +GET /api/bidragssatser
        +GET /api/bond-prices
        +GET /api/reference-rates/{type}
        +POST /api/market-rates/refresh
    }

    class market_data_py {
        +get_market_rates() MarketRates
        +get_nominal_rate(LoanType) Decimal
        +get_reference_rate(str) Decimal
        +get_bidragssatser(str, str) list~BidragssatsEntry~
        +get_bond_prices() dict
        +build_preset_from_market(MarketRates) CalculatorInput
        -_fetch_* (urllib)
        -_cache (per-source JSON)
        -lookup_bidragssats(BidragssatsKey) Decimal
        -_normalize_column_to_loan_type(str) LoanType
    }

    class mcp_server_py {
        +get_market_rates() dict
    }

    class engine_py {
        +calculate(CalculatorInput) CalculatorResult
        +PRESETS: hardcoded fallback defaults
    }

    class MarketRates {
        +fetched_at: datetime
        +nominal_rates: NominalRates
        +reference_rates: ReferenceRates
        +bank_rate: Decimal
        +bidragssatser: list~BidragssatsEntry~
    }

    class CalculatorInput {
        +alternatives: list~LoanSpec~
        +tax_rate: Decimal
        +rate_shocks: list~Decimal~
    }

    server_py --> market_data_py : endpoints call public accessors
    mcp_server_py --> market_data_py : get_market_rates tool
    market_data_py --> engine_py : build_preset_from_market produces CalculatorInput
    market_data_py ..> MarketRates : reads/writes
    market_data_py ..> CalculatorInput : produces
    engine_py ..> CalculatorInput : consumes
```

## New file: `src/boligregner/market_data.py`

Single module containing data models, fetchers, cache, lookup, and adapter. No new dependencies
(stdlib `urllib`, `json`, `csv`, `re`, `datetime`, `decimal`). All monetary values stay
`Decimal`. All functions are **synchronous** (`def`, not `async def`) — the codebase is fully
synchronous (server endpoints, MCP tools, engine are all `def`); sync fetchers avoid
`asyncio.run()` bridging.

### Data models (in `market_data.py`, not `models.py`)

Market-data schemas live in `market_data.py`, not `models.py`. This keeps `models.py` for
engine schemas only, avoiding divergent-change (two unrelated change axes in one file).

```python
class LTVBand(str, Enum):
    """LTV bracket for bidragssats lookup."""
    ZERO_TO_40 = "0-40"
    FORTY_TO_60 = "40-60"
    OVER_60 = "over-60"

class Institute(str, Enum):
    """Danish realkredit institutes."""
    JYSKE = "jyske"
    NYKREDIT = "nykredit"
    NORDEA = "nordea"
    RD = "rd"

class BidragssatsKey(BaseModel):
    """Lookup key for bidragssats — bundles the clump that travels together."""
    institute: Institute
    loan_type: LoanType
    ltv_band: LTVBand
    afdragsfrihed: bool

class BidragssatsEntry(BidragssatsKey):
    """One bidragssats value, keyed by (institute, loan_type, ltv_band, afdragsfrihed)."""
    bidragssats: Decimal

class NominalRates(BaseModel):
    """Nominal rates keyed by LoanType — typed replacement for dict[str, Decimal].
    Assumes LoanType extended with F2, F4, F6, F10 (follow-up code change)."""
    f1: Decimal | None = None
    f2: Decimal | None = None
    f3: Decimal | None = None
    f4: Decimal | None = None
    f5: Decimal | None = None
    f6: Decimal | None = None
    f10: Decimal | None = None

class ReferenceRates(BaseModel):
    """Reference rates keyed by rate type — typed replacement for dict[str, Decimal]."""
    cibor_3m: Decimal | None = None
    cibor_6m: Decimal | None = None
    cita_3m: Decimal | None = None
    destr: Decimal | None = None

class MarketRates(BaseModel):
    """Snapshot of all sourced market data."""
    fetched_at: datetime
    # Nominal rates (LoanSpec.rate)
    nominal_rates: NominalRates = Field(default_factory=NominalRates)
    fixed_coupon_rate: Decimal | None = None
    # Bond prices (LoanSpec.price) — FIXED only; flexlån always 100
    fixed_bond_price: Decimal | None = None
    # Reference rates (LoanSpec.reference_rate)
    reference_rates: ReferenceRates = Field(default_factory=ReferenceRates)
    # Bank rate
    bank_rate: Decimal | None = None
    # Bidragssatser (all institutes, all brackets)
    bidragssatser: list[BidragssatsEntry] = Field(default_factory=list)
    # Source metadata
    sources: dict[str, str] = Field(default_factory=dict)  # source_name → timestamp
```

Internal fetcher result types (not part of public interface):

```python
class DSTRates(BaseModel):
    """Effective rates + avg bidrag + ÅOP from DST Statbank DNRNURI.
    Validation data only — not used to populate LoanSpec fields."""
    f3_effective: Decimal | None = None
    f5_effective: Decimal | None = None
    fixed_effective: Decimal | None = None
    f3_bidrag_avg: Decimal | None = None
    f5_bidrag_avg: Decimal | None = None
    fixed_bidrag_avg: Decimal | None = None

class NordeaBondPrices(BaseModel):
    """Bond prices from Nordea's '500.000' page.
    Fixed kurs maps to LoanSpec.price; F3/F5 kurs are underlying bond prices (not LoanSpec.price for flexlån)."""
    fixed_coupon: Decimal | None = None
    fixed_kurs: Decimal | None = None
    f3_kontantrente: Decimal | None = None  # Reference only
    f5_kontantrente: Decimal | None = None  # Reference only
```

### Fetchers (internal, not part of public interface)

Fetchers are private functions prefixed with `_`. The public interface is `get_market_rates()`
and `build_preset_from_market()`. This keeps the test surface small: tests exercise the public
interface with fixture data, not individual fetchers.

```python
def _fetch_dst_effective_rates() -> DSTRates:
    """POST api.statbank.dk/v1/data/DNRNURI — effective rates + avg bidrag + ÅOP.
    Returns validation data (not used to populate LoanSpec.rate)."""

def _fetch_ecb_bank_rate() -> Decimal:
    """GET data-api.ecb.europa.eu — banklån rate (AAR)."""

def _fetch_mybanker_bidragssatser() -> list[BidragssatsEntry]:
    """GET mybanker.dk/sammenlign/bolig/bidragssatser — parse 5 HTML tables.
    Returns per-institute × per-LTV × per-loan-type × per-afdragsfrihed entries."""

def _fetch_rd_nominal_rates() -> NominalRates:
    """GET rd.dk/laantyper/flexlaan-k/renteudvikling — parse HTML tables.
    Returns typed NominalRates, not dict[str, Decimal]."""

def _fetch_nordea_bond_prices() -> NordeaBondPrices:
    """GET nordea.dk/.../hvad-koster-det-at-laane-en-halv-million.html
    Returns fixed bond kurs + coupon, F3/F5 kontantrente (for reference only)."""

def _fetch_finansdanmark_obligationsrente() -> Decimal:
    """Scrape finansdanmark.dk/.../obligationsrenter for XLSX URL, download XLSX.
    Returns lang obligationsrente (proxy for fixed coupon)."""

def _fetch_jyske_reference_rates() -> ReferenceRates:
    """GET jyskebank.dk/bolig/boliglaan/referencerenter via urllib (Cloudflare blocks curl).
    Returns typed ReferenceRates, not dict[str, Decimal]."""

def _fetch_destr_rate() -> Decimal:
    """POST api.statbank.dk/v1/data/DNRENTD — DESTR Referencerente (daily).
    INSTRUMENT=DESNAA, OPGOER=E, TID=last available date."""
```

### Cache
Per-source JSON files in `data/cache/`. Each source has its own TTL and cache file, so
a slow or unavailable source doesn't block others. Split endpoints read only their cache file.

```python
CACHE_TTL = {
    "dst":           24 * 3600,   # monthly data
    "ecb":           24 * 3600,
    "mybanker":      24 * 3600,
    "rd":            24 * 3600,
    "nordea":        24 * 3600,
    "finansdanmark": 24 * 3600,
    "jyske_ref":     12 * 3600,   # CIBOR/CITA set quarterly; page updated at each rate-setting
    "destr":         6 * 3600,    # daily rate
}
# Each source writes to data/cache/{source_name}.json
# Endpoints read only their source's cache — no single-file bottleneck.
```

```python
def get_market_rates(force_refresh: bool = False) -> MarketRates:
    """Return cached MarketRates (full snapshot), or fetch all sources if expired.
    On fetch failure, return last-good cached value for that source.
    Used by build_preset_from_market() and MCP tool."""

def get_nominal_rate(loan_type: LoanType) -> Decimal | None:
    """Return cached nominal rate for a single loan type. Reads only RD.dk cache."""

def get_reference_rate(rate_type: str) -> Decimal | None:
    """Return cached reference rate (cibor_3m, cibor_6m, cita_3m, destr).
    Reads only Jyske/DESTR cache."""

def get_bidragssatser(institute: str | None = None, loan_type: str | None = None) -> list[BidragssatsEntry]:
    """Return cached bidragssatser, optionally filtered. Reads only Mybanker.dk cache."""

def get_bond_prices() -> dict:
    """Return cached fixed-rate bond prices. Reads only Nordea cache."""

def refresh_market_rates() -> MarketRates:
    """Force refresh all sources. Used by /api/market-rates/refresh."""
```

### Bidragssats lookup

```python
def lookup_bidragssats(
    key: BidragssatsKey,
    rates: MarketRates,
) -> Decimal:
    """Look up bidragssats from cached MarketRates.
    Falls back to 0.006 (current default) if not found."""
```

### Loan-type normalization

Maps Mybanker.dk bidragssats column labels to `LoanType`. Mybanker.dk groups bidragssatser
by fixation-period ranges (e.g. "Flekslån F3-F4" covers both F3 and F4 products). Each
`LoanType` is a single discrete product (F3 = 3-year rate reset).

**Prerequisite:** The `LoanType` enum currently has `F1`, `F3`, `F5` only. Mybanker.dk
columns reference F2, F4, F6, F10 as distinct products. Before implementing this module,
extend `LoanType` with `F2`, `F4`, `F6`, `F10` (code change in `models.py` + engine support
for amortization/rate shocks). This is out of scope for this docs-only PR — tracked as a
follow-up. The normalization map below assumes the extended enum.

```python
# Mybanker.dk bidragssats column → LoanType(s) it covers
# Assumes LoanType extended with F2, F4, F6, F10 (follow-up code change)
_BIDRAGSSATS_COLUMN_TO_LOAN_TYPES = {
    "Flekslån F1": [LoanType.F1],
    "Flekslån F1-F2": [LoanType.F1, LoanType.F2],
    "Flekslån F3": [LoanType.F3],
    "Flekslån F3-F4": [LoanType.F3, LoanType.F4],
    "Flekslån F5": [LoanType.F5],
    "Flekslån F5-F6": [LoanType.F5, LoanType.F6],
    "Flekslån F5-F10": [LoanType.F5, LoanType.F6, LoanType.F10],
    "Flekslån F5 & Kort Rente": [LoanType.F5],
    "Fastforrentet lån": [LoanType.FIXED],
}

# Reverse lookup: which bidragssats column to use for a given LoanType
_LOAN_TYPE_TO_BIDRAGSSATS_COLUMN = {
    LoanType.F1: "Flekslån F1-F2",     # F1 shares a column with F2
    LoanType.F2: "Flekslån F1-F2",
    LoanType.F3: "Flekslån F3-F4",     # F3 shares a column with F4
    LoanType.F4: "Flekslån F3-F4",
    LoanType.F5: "Flekslån F5",
    LoanType.F6: "Flekslån F5-F6",
    LoanType.F10: "Flekslån F5-F10",
    LoanType.FIXED: "Fastforrentet lån",
}
```

**Note:** An F3 is always exactly one product (3-year rate reset). Mybanker.dk's "Flekslån F3-F4"
column is a bidragssats bracket covering both F3 and F4 — it is not itself a product range.
The normalization maps the column to the `LoanType`(s) it covers, not the reverse.

## New API endpoints (in `server.py`)

Existing endpoints are `def` (sync). New endpoints match — sync `def`, not `async def`.

Each data type gets its own endpoint with its own cache entry. Splitting by data type
means a slow or unavailable source (e.g. Jyske Bank behind Cloudflare) doesn't block
other data types. Each endpoint returns only the data it sources, not the full snapshot.

```python
@app.get("/api/market-rates/{loan_type}", response_model=NominalRates | Decimal)
def api_market_rates(loan_type: str) -> dict:
    """Current nominal rate for a specific loan type (F1, F3, F5, FIXED).
    Cached per loan type. Returns {"loan_type": "f3", "rate": 0.0239, "fetched_at": ...}."""

@app.get("/api/bidragssatser")
def api_bidragssatser(institute: str | None = None, loan_type: str | None = None) -> dict:
    """Bidragssatser by institute × LTV × loan type × afdragsfrihed.
    Returns flat list of BidragssatsEntry dicts, optionally filtered."""

@app.get("/api/bond-prices")
def api_bond_prices() -> dict:
    """Current fixed-rate bond prices (kurs). Flexlån always 100 (par)."""

@app.get("/api/reference-rates/{rate_type}")
def api_reference_rates(rate_type: str) -> dict:
    """Reference rate (CIBOR 3M/6M, CITA 3M, DESTR). Cached per source."""

@app.post("/api/market-rates/refresh")
def api_market_rates_refresh() -> dict:
    """Force refresh all sources. Returns fresh snapshot."""
```

## New MCP tool (in `mcp_server.py`)

```python
@mcp.tool()
def get_market_rates() -> dict[str, Any]:
    """Get current Danish mortgage market data: nominal rates, bidragssatser,
    bond prices, reference rates (CIBOR/CITA/DESTR), and bank rate.
    Data is cached with 24h TTL; call refresh_market_rates to force update."""
```

## Changes to existing files

### `engine.py` — unchanged

engine.py is the single source of truth for mortgage math. It stays untouched. PRESETS remain
as hardcoded fallback defaults — they preserve backward compatibility and let the calculator
work offline.

### `market_data.py` — adapter to engine

`build_preset_from_market()` lives in `market_data.py`, not `engine.py`. This is a thin adapter
that reads `MarketRates` and produces a `CalculatorInput` — the engine stays deep (no
dependency on market data).

```python
def build_preset_from_market(
    rates: MarketRates,
    preset_name: str = "default",
    institute: Institute = Institute.NYKREDIT,
    ltv_band: LTVBand = LTVBand.ZERO_TO_40,
) -> CalculatorInput:
    """Build a CalculatorInput using live market data instead of hardcoded values.
    Preserves preset structure (same alternatives, same bank share, same maturity).
    Replaces: rates, bidragssatser, bond prices, bank rate.
    Lives in market_data.py (adapter), not engine.py (deep module)."""
```

### `models.py` — unchanged

No new models added to `models.py`. Market-data schemas live in `market_data.py`.

### `server.py` — New endpoints

Five new sync endpoints (described above). No changes to existing endpoints.

### `mcp_server.py` — New tool

One new sync tool `get_market_rates` (described above). No changes to existing tools.

## Source → field mapping

| LoanSpec field | Source | Fetcher | What it returns |
|---|---|---|---|
| `rate` (F1) | RD.dk renteudvikling | `_fetch_rd_nominal_rates()` | 0.0227 (2.27% Apr 2026) |
| `rate` (F3) | RD.dk renteudvikling | `_fetch_rd_nominal_rates()` | 0.0239 (2.39% Apr 2026) |
| `rate` (F5) | RD.dk renteudvikling | `_fetch_rd_nominal_rates()` | 0.0266 (2.66% Apr 2026) |
| `rate` (FIXED) | Nordea "500.000" page coupon, or Finans Danmark proxy | `_fetch_nordea_bond_prices()` | 0.04 (4.00% Oct 2026) |
| `rate` (T-lån) | Not sourced — uses underlying flexlån rate (typically F5) | — | User/config input; T-lån is an F-loan with fixed ydelse, not a separate rate product |
| `price` (FIXED) | Nordea "500.000" page kurs | `_fetch_nordea_bond_prices()` | 95.45 (Oct 2026) |
| `price` (F1/F3/F5) | Hardcoded 100 (par) | — | Flexlån always trade at par |
| `bidragssats` | Mybanker.dk | `_fetch_mybanker_bidragssatser()` | Per LTV × loan type × afdragsfrihed |
| `reference_rate` (CIBOR) | Jyske Bank referencerenter | `_fetch_jyske_reference_rates()` | 0.0227 (CIBOR 3M, Jun 2026) |
| `reference_rate` (CITA) | Jyske Bank referencerenter | `_fetch_jyske_reference_rates()` | 0.018743 (CITA 3M, Jul 2026) |
| `reference_rate` (DESTR) | DST Statbank DNRENTD | `_fetch_destr_rate()` | 0.02033 (Sep 30 2026) |
| `margin` | Stays hardcoded (0.0025) | — | Institute-specific, no public source |
| Bank `rate` | ECB MIR API | `_fetch_ecb_bank_rate()` | 0.0403 (4.03% Aug 2026) |
| `issue_costs_pct` | Stays hardcoded | — | No public API; institute-specific |

T-lån rate note: T-lån is an F-loan variant — fixed monthly ydelse with variable duration
instead of fixed duration and variable ydelse. It uses the same underlying flexlån rate
(typically F5). The `rate` is a user/config input, not a separately sourced value.
The `fixed_ydelse` also stays as user input.

## CIBOR/CITA/DESTR sourcing details

These three loan types (`LoanType.CIBOR`, `LoanType.CITA`, `LoanType.DESTR`) need
`reference_rate` + `margin`. The margin is institute-specific and stays hardcoded (0.0025
in presets). The reference rate is sourced:

| Loan type | Reference rate | Source | API | Cadence |
|---|---|---|---|---|
| CIBOR 3M | CIBOR 3M | Jyske Bank referencerenter (urllib) | HTML scrape | Quarterly (set at auction) |
| CIBOR 6M | CIBOR 6M | Jyske Bank referencerenter (urllib) | HTML scrape | Quarterly |
| CITA 3M | CITA 3M | Jyske Bank referencerenter (urllib) | HTML scrape | Quarterly |
| DESTR | DESTR Referencerente | DST Statbank DNRENTD (DESNAA) | JSON/CSV POST | Daily |

**CITA 6M note:** Jyske Bank's referencerenter page only publishes CITA 3M, not CITA 6M.
CITA 6M is not used in any preset. No `cita_6m` field in `ReferenceRates`.

**CIBOR/CITA availability notes:**
- DFBF (Danish Financial Benchmark Facility) owns CIBOR, CITA, SWAP, Tom/Next.
- Since June 2022, 24h-delayed and historical CIBOR/CITA data requires free registration
  on the DFBF information portal. Not suitable for automated scraping.
- Finans Danmark no longer displays reference rates on their website.
- Jyske Bank's referencerenter page is the only public, no-registration source for CIBOR/CITA.
  It requires `urllib` (not `curl`) due to Cloudflare.
- CIBOR is in transition (Nov 2025 announcement): Finance Denmark is working on a
  transition away from CIBOR to transaction-based rates (DESTR). No end date set yet.
- DESTR is published daily by Danmarks Nationalbank, available via DST Statbank DNRENTD
  (INSTRUMENT=DESNAA, OPGOER=E, daily observations). No registration required.

## Testing strategy

### Unit tests (`tests/test_market_data.py`)

- **Parser tests**: Feed saved HTML/JSON fixtures to each parser, verify output.
  Fixtures stored as `tests/fixtures/{source_name}.html` / `.json` / `.csv`.
- **Cache tests**: Verify TTL expiry, last-good fallback on fetch failure.
- **Bidragssats lookup tests**: Verify lookup returns correct rate for
  `BidragssatsKey(institute, loan_type, ltv_band, afdragsfrihed)`.
- **Normalization tests**: Verify bidragssats columns resolve to correct LoanType.
- **Integration**: `build_preset_from_market()` with mock MarketRates produces valid
  CalculatorInput that passes model validation.

Tests call `get_market_rates()` with fixture data, not individual `_fetch_*` functions.
This tests the public interface, not implementation details.

### Smoke test

```bash
# Verify live data fetch works (no network in CI — skip if offline)
uv run python -c "from boligregner.market_data import get_market_rates; print(get_market_rates())"
```

### Existing tests stay unchanged

All 32 existing engine tests pass unchanged — they use hardcoded PRESETS, not market data.
The `build_preset_from_market()` function is opt-in.

## Implementation order

1. **`market_data.py`**: Data models (`LTVBand`, `Institute`, `BidragssatsKey`,
   `BidragssatsEntry`, `NominalRates`, `ReferenceRates`, `MarketRates`).
2. **`market_data.py`**: Fetchers (one at a time, each with fixture-based test).
3. **`market_data.py`**: Per-source cache + public accessors (`get_market_rates()`,
   `get_nominal_rate()`, `get_reference_rate()`, `get_bidragssatser()`,
   `get_bond_prices()`) + `lookup_bidragssats()`.
4. **`market_data.py`**: `build_preset_from_market()` (adapter, not in engine.py).
5. **`server.py`**: Add 5 new sync endpoints (per-loan-type rates, bidragssatser, bond-prices, reference-rates, refresh).
6. **`mcp_server.py`**: Add `get_market_rates` sync tool.
7. **`tests/test_market_data.py`**: Parser tests with fixtures.
8. **`docs/2026-10-05-market-data-sources.md`**: Already written.

## Risks

| Risk | Mitigation |
|---|---|
| Source HTML structure changes | Parse defensively; log parse failures; fall back to cache |
| Source goes offline | Last-good cache; hardcoded PRESETS remain as fallback |
| Cloudflare blocks (Jyske) | Use `urllib` with browser User-Agent; documented workaround |
| Finans Danmark XLSX URL changes weekly | Scrape the page to discover the link each time |
| CIBOR transition (no end date) | DESTR is the replacement; CIBOR fetcher may need removal |
| F5 negative rate (-0.10% from Oct 2021 auction) | `LoanSpec.rate` validator rejects `rate < 0`. `build_preset_from_market` must clamp negative rates to 0 before passing to `LoanSpec`, or skip the alternative. The engine does NOT handle negative `rate` values. |
