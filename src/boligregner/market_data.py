"""Live market data integration layer for boligregner-os.

Fetches, caches, and exposes Danish realkredit market data from public sources:
nominal rates (RD.dk), bidragssatser (Mybanker.dk), bond prices (Nordea),
reference rates (Jyske Bank, DST), bank rate (ECB), and obligationsrente
(Finans Danmark).  All monetary values are ``Decimal``.

The public interface is small and synchronous:

    get_market_rates(force_refresh=False) -> MarketRates
    get_nominal_rate(loan_type)           -> Decimal | None
    get_reference_rate(rate_type)         -> Decimal | None
    get_bidragssatser(institute, ...)     -> list[BidragssatsEntry]
    get_bond_prices()                     -> dict
    refresh_market_rates()                -> MarketRates
    lookup_bidragssats(key, rates)        -> Decimal
    build_preset_from_market(rates, ...)   -> CalculatorInput

Each fetcher is split into ``_fetch_X`` (HTTP I/O) and ``_parse_X`` (pure
function).  Tests exercise ``_parse_X`` with saved fixture files — no network.
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from .engine import PRESETS
from .models import CalculatorInput, LoanType

# ─── Constants ───────────────────────────────────────────────────────

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DST_URL = "https://api.statbank.dk/v1/data"
ECB_URL = (
    "https://data-api.ecb.europa.eu/service/data/"
    "MIR/M.DK.B.A2C.A.R.A.2250.DKK.N"
    "?lastNObservations=1&format=csvdata"
)
MYBANKER_URL = "https://www.mybanker.dk/sammenlign/bolig/bidragssatser"
RD_URL = "https://rd.dk/laantyper/flexlaan-k/renteudvikling"
NORDEA_URL = (
    "https://www.nordea.dk/privat/produkter/boliglaan/"
    "hvad-koster-det-at-laane-en-halv-million.html"
)
FINANSDANMARK_URL = (
    "https://finansdanmark.dk/tal-og-data/boligstatistik/obligationsrenter"
)
JYSKE_URL = "https://www.jyskebank.dk/bolig/boliglaan/referencerenter"

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
# parents[2] is the repo root in the src/ checkout layout.  In an installed
# package this resolves inside site-packages, where the process cannot write.

CACHE_TTL: dict[str, int] = {
    "dst": 24 * 3600,
    "ecb": 24 * 3600,
    "mybanker": 24 * 3600,
    "rd": 24 * 3600,
    "nordea": 24 * 3600,
    "finansdanmark": 24 * 3600,
    "jyske_ref": 12 * 3600,
    "destr": 6 * 3600,
}

_DEFAULT_BIDRAGSSATS = Decimal("0.006")


# ─── Data models ─────────────────────────────────────────────────────


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
    """Nominal rates keyed by LoanType (fraction, e.g. 0.0239 = 2.39%)."""

    f1: Decimal | None = None
    f2: Decimal | None = None
    f3: Decimal | None = None
    f4: Decimal | None = None
    f5: Decimal | None = None
    f6: Decimal | None = None
    f10: Decimal | None = None


class ReferenceRates(BaseModel):
    """Reference rates keyed by rate type (fraction)."""

    cibor_3m: Decimal | None = None
    cibor_6m: Decimal | None = None
    cita_3m: Decimal | None = None
    destr: Decimal | None = None


class DSTRates(BaseModel):
    """Effective rates + avg bidrag from DST Statbank DNRNURI.

    Validation data only — not used to populate LoanSpec.rate.
    """

    f3_effective: Decimal | None = None
    f5_effective: Decimal | None = None
    fixed_effective: Decimal | None = None
    f3_bidrag_avg: Decimal | None = None
    f5_bidrag_avg: Decimal | None = None
    fixed_bidrag_avg: Decimal | None = None


class NordeaBondPrices(BaseModel):
    """Bond prices from Nordea's page.

    fixed_kurs maps to LoanSpec.price; f3/f5_kontantrente are for reference only.
    """

    fixed_coupon: Decimal | None = None
    fixed_kurs: Decimal | None = None
    f3_kontantrente: Decimal | None = None
    f5_kontantrente: Decimal | None = None


class MarketRates(BaseModel):
    """Snapshot of all sourced market data."""

    fetched_at: datetime
    nominal_rates: NominalRates = Field(default_factory=NominalRates)
    fixed_coupon_rate: Decimal | None = None
    fixed_bond_price: Decimal | None = None
    reference_rates: ReferenceRates = Field(default_factory=ReferenceRates)
    bank_rate: Decimal | None = None
    bidragssatser: list[BidragssatsEntry] = Field(default_factory=list)
    dst_rates: DSTRates | None = None
    lang_obligationsrente: Decimal | None = None
    sources: dict[str, str] = Field(default_factory=dict)


# ─── Danish decimal helper ───────────────────────────────────────────


def _parse_danish_decimal(s: str) -> Decimal | None:
    """Parse a Danish-formatted decimal string.

    Handles: BOM, trailing '%', comma decimal separator, empty/'-'/'..' values.
    Returns the raw Decimal (not divided by 100 — caller does that).
    """
    if s is None:
        return None
    cleaned = s.strip().lstrip("\ufeff")
    cleaned = cleaned.rstrip("%").strip()
    if not cleaned or cleaned in ("-", "..", "…", "−"):
        return None
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


# ─── HTTP helpers ────────────────────────────────────────────────────


def _http_get(url: str, timeout: int = 15) -> str:
    """GET with browser User-Agent.  Returns body as str (utf-8-sig)."""
    req = Request(url, headers={"User-Agent": _BROWSER_UA})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return body.decode("utf-8-sig", errors="replace")


def _http_get_bytes(url: str, timeout: int = 30) -> bytes:
    """GET with browser User-Agent.  Returns raw bytes (for XLSX)."""
    req = Request(url, headers={"User-Agent": _BROWSER_UA})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post_json(url: str, body: dict, timeout: int = 15) -> str:
    """POST JSON with browser User-Agent.  Returns body as str (utf-8-sig)."""
    data = json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "User-Agent": _BROWSER_UA,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8-sig", errors="replace")


# ─── Fetcher: DST DNRNURI (effective rates) ──────────────────────────


def _fetch_dst_effective_rates() -> DSTRates:
    """POST api.statbank.dk DNRNURI — effective rates + avg bidrag + ÅOP."""
    body = {
        "table": "DNRNURI",
        "format": "CSV",
        "variables": [
            {"code": "DATA", "values": ["AL51EFFR", "AL51BIDS", "AL50AAOP"]},
            {"code": "INDSEK", "values": ["1400"]},
            {"code": "VALUTA", "values": ["DKK"]},
            {"code": "RENTFIX", "values": ["3A", "5A", "S10A"]},
            {"code": "TID", "values": ["(1)"]},
        ],
    }
    csv_text = _http_post_json(DST_URL, body)
    return _parse_dst_dnrnuri(csv_text)


def _parse_dst_dnrnuri(csv_text: str) -> DSTRates:
    """Parse DST DNRNURI semicolon-delimited CSV.

    Columns: DATA;INDSEK;VALUTA;RENTFIX;TID;INDHOLD
    DATA values: 'Effektiv rentesats inkl. bidrag', 'Bidragssats', 'AOP'
    RENTFIX values: '3 år'->F3, '5 år'->F5, '10 år'->FIXED
    """
    rates = DSTRates()
    for line in csv_text.strip().splitlines()[1:]:  # skip header
        parts = line.split(";")
        if len(parts) < 6:
            continue
        data_label = parts[0]
        rentfix = parts[3]
        value = _parse_danish_decimal(parts[5])
        if value is None:
            continue
        fraction = value / Decimal(100)

        is_effective = "Effektiv rentesats" in data_label
        is_bidrag = "Bidragssats" in data_label

        if is_effective:
            if "3 år" in rentfix and "5 år" not in rentfix:
                rates.f3_effective = fraction
            elif "5 år" in rentfix:
                rates.f5_effective = fraction
            elif "10 år" in rentfix:
                rates.fixed_effective = fraction
        elif is_bidrag:
            if "3 år" in rentfix and "5 år" not in rentfix:
                rates.f3_bidrag_avg = fraction
            elif "5 år" in rentfix:
                rates.f5_bidrag_avg = fraction
            elif "10 år" in rentfix:
                rates.fixed_bidrag_avg = fraction
    return rates


# ─── Fetcher: ECB bank rate ──────────────────────────────────────────


def _fetch_ecb_bank_rate() -> Decimal:
    """GET ECB Data Portal — banklån rate (AAR)."""
    csv_text = _http_get(ECB_URL)
    return _parse_ecb_bank_rate(csv_text)


def _parse_ecb_bank_rate(csv_text: str) -> Decimal:
    """Parse ECB SDMX CSV — extract OBS_VALUE from the single data row."""
    lines = csv_text.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("ECB CSV has no data rows")
    header = lines[0].split(",")
    obs_idx = header.index("OBS_VALUE")
    data_row = lines[1].split(",")
    value = _parse_danish_decimal(data_row[obs_idx])
    if value is None:
        raise ValueError("ECB CSV OBS_VALUE is not parseable")
    return value / Decimal(100)


# ─── Fetcher: Mybanker.dk bidragssatser ──────────────────────────────

_INSTITUTE_HEADING_MAP = {
    "jyske realkredit": Institute.JYSKE,
    "nykredit": Institute.NYKREDIT,
    "totalkredit": Institute.NYKREDIT,
    "nordea kredit": Institute.NORDEA,
    "realkredit danmark": Institute.RD,
}

# Mybanker.dk bidragssats column label → LoanType(s) it covers.
# Only existing LoanType members; F2/F4/F6/F10 are skipped (no enum members).
_BIDRAGSSATS_COLUMN_TO_LOAN_TYPES: dict[str, list[LoanType]] = {
    "Fastforrentet lån": [LoanType.FIXED],
    "Flekslån F1": [LoanType.F1],
    "Flekslån F1-F2": [LoanType.F1],
    "Flekslån F2-F4": [LoanType.F3],
    "Flekslån F3": [LoanType.F3],
    "Flekslån F3-F4": [LoanType.F3],
    "Flekslån F5": [LoanType.F5],
    "Flekslån F5-F6": [LoanType.F5],
    "Flekslån F5-F10": [LoanType.F5],
    "Flekslån F5 & Kort Rente": [LoanType.F5],
    "Flekslån F5 &amp; Kort Rente": [LoanType.F5],
}


def _normalize_ltv_band(text: str) -> LTVBand | None:
    """Parse an LTV band string from Mybanker.dk row labels."""
    t = text.strip().lower().rstrip("%").strip()
    if "0-40" in t or (t.startswith("0") and "40" in t):
        return LTVBand.ZERO_TO_40
    if "40-60" in t:
        return LTVBand.FORTY_TO_60
    if "over 60" in t or "over-60" in t or ">60" in t or "60-" in t:
        return LTVBand.OVER_60
    return None


class _MybankerParser(HTMLParser):
    """Parse the Mybanker.dk bidragssatser page.

    The page has 5 tables: table 0 is an overview (blended rates), tables 1-4
    are per-institute detail.  Each per-institute table has:
      - Row 0: group headers (loan-type labels, may span multiple columns)
      - Row 1: sub-headers ("Med afdrag" / "Uden afdrag")
      - Rows 2+: LTV bands with bidragssats values
    """

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[BidragssatsEntry] = []
        self._tables: list[list[list[str]]] = []
        self._headings: list[str] = []
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] = []
        self._in_cell = False
        self._colspan = 1
        self._in_h3 = False
        self._h3_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table":
            self._cur_table = []
            self._cur_row = []
            self._cur_cell = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._in_cell = True
            self._cur_cell = []
            cs = attr_dict.get("colspan", "1")
            self._colspan = int(cs) if cs and cs.isdigit() else 1
        elif tag == "h3":
            self._in_h3 = True
            self._h3_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._cur_table is not None:
            self._tables.append(self._cur_table)
            self._cur_table = None
        elif tag == "tr" and self._cur_row is not None and self._cur_table is not None:
            self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag in ("td", "th") and self._in_cell:
            text = "".join(self._cur_cell).strip()
            for _ in range(self._colspan):
                self._cur_row.append(text)
            self._in_cell = False
            self._cur_cell = []
            self._colspan = 1
        elif tag == "h3" and self._in_h3:
            heading = "".join(self._h3_text).strip()
            if heading:
                self._headings.append(heading)
            self._in_h3 = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cur_cell.append(data)
        if self._in_h3:
            self._h3_text.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._in_cell:
            self._cur_cell.append(f"&{name};")
        if self._in_h3:
            self._h3_text.append(f"&{name};")

    def build_entries(self) -> list[BidragssatsEntry]:
        """Extract BidragssatsEntry objects from parsed tables."""
        entries: list[BidragssatsEntry] = []
        # Tables 1-4 are per-institute (skip table 0 = overview)
        for tbl_idx in range(1, min(len(self._tables), 5)):
            table = self._tables[tbl_idx]
            if len(table) < 3:
                continue
            heading_idx = tbl_idx - 1
            if heading_idx >= len(self._headings):
                continue
            heading = self._headings[heading_idx]
            institute = self._match_institute(heading)
            if institute is None:
                continue
            # Row 0: group headers (loan-type labels, may include spacer columns)
            # Row 1: sub-headers (Med/Uden afdrag)
            # The group row may have more columns than the sub row due to
            # empty separator cells between groups.  Build a filtered list of
            # non-empty group labels that aligns 1:1 with the sub-row.
            group_row = table[0]
            sub_row = table[1] if len(table) > 1 else []

            # Filter out empty/separator cells, deduplicate consecutive
            # repeats (colspan expansion produces duplicates)
            group_labels: list[str] = []
            for g in group_row:
                if g and (not group_labels or group_labels[-1] != g):
                    group_labels.append(g)
            # First element is 'Ejerbolig' (row label), skip it
            if group_labels and "Ejerbolig" in group_labels[0]:
                group_labels = group_labels[1:]

            # Build column_index → (loan_type_label, afdragsfrihed) map
            col_map: list[tuple[str, bool]] = []
            group_idx = 0
            for col_idx in range(len(sub_row)):
                sub_label = sub_row[col_idx] if col_idx < len(sub_row) else ""
                if col_idx == 0:
                    continue  # skip row label
                # Each group label maps to 2 sub-columns (Med + Uden afdrag)
                label = group_labels[group_idx] if group_idx < len(group_labels) else ""
                afdragsfrihed = "uden" in sub_label.lower()
                col_map.append((label, afdragsfrihed))
                # Advance group_idx every 2 columns (Med + Uden afdrag)
                if "uden" in sub_label.lower():
                    group_idx += 1

            # Rows 2+: LTV bands with values
            for data_row in table[2:]:
                if not data_row:
                    continue
                ltv = _normalize_ltv_band(data_row[0])
                if ltv is None:
                    continue
                for col_idx in range(1, len(data_row)):
                    map_idx = col_idx - 1
                    if map_idx >= len(col_map):
                        continue
                    label, afdragsfrihed = col_map[map_idx]
                    loan_types = _BIDRAGSSATS_COLUMN_TO_LOAN_TYPES.get(label)
                    if not loan_types:
                        continue
                    value = _parse_danish_decimal(data_row[col_idx])
                    if value is None:
                        continue
                    fraction = value / Decimal(100)
                    for lt in loan_types:
                        entries.append(
                            BidragssatsEntry(
                                institute=institute,
                                loan_type=lt,
                                ltv_band=ltv,
                                afdragsfrihed=afdragsfrihed,
                                bidragssats=fraction,
                            )
                        )
        return entries

    @staticmethod
    def _match_institute(heading: str) -> Institute | None:
        key = heading.lower().replace("\xad", "").strip()
        for h_text, inst in _INSTITUTE_HEADING_MAP.items():
            if h_text in key:
                return inst
        return None


def _fetch_mybanker_bidragssatser() -> list[BidragssatsEntry]:
    """GET mybanker.dk — parse 5 HTML tables for bidragssatser.

    Raises ValueError when the parse yields no entries (page layout
    change), so the caller treats it as a fetch failure and keeps the
    last-good cache instead of caching an empty success.
    """
    html_text = _http_get(MYBANKER_URL)
    return _parse_mybanker_bidragssatser(html_text)


def _parse_mybanker_bidragssatser(html_text: str) -> list[BidragssatsEntry]:
    """Parse Mybanker.dk HTML and return per-institute × per-LTV entries.

    An empty result means the page layout changed (no tables matched).
    _fetch_mybanker_bidragssatser treats that as a fetch failure so the
    stale-cache fallback kicks in, instead of caching success.
    """
    parser = _MybankerParser()
    parser.feed(html_text)
    return parser.build_entries()


# ─── Fetcher: RD.dk nominal rates ────────────────────────────────────


def _fetch_rd_nominal_rates() -> NominalRates:
    """GET rd.dk — parse HTML tables for nominal flexlån rates."""
    html_text = _http_get(RD_URL)
    return _parse_rd_nominal_rates(html_text)


# Maps RD.dk column header loan-type prefix to NominalRates field name.
_RD_LOAN_TYPE_MAP = {
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f10": "f10",
}


def _parse_rd_nominal_rates(html_text: str) -> NominalRates:
    """Parse RD.dk HTML tables 0-1 (DKK med/uden afdrag) for nominal rates.

    Table 0: med afdrag, table 1: uden afdrag.  Row 1 is the most recent.
    Columns: F1, F2, F3, F4, F5, F10.  We take the med afdrag values (table 0)
    as the primary nominal rate.  Rates are percentages (2,27 = 2.27%).
    """
    parser = _SimpleTableParser()
    parser.feed(html_text)
    tables = parser.tables
    if len(tables) < 1:
        return NominalRates()

    rates = NominalRates()
    # Table 0: med afdrag (DKK) — primary source
    table = tables[0]
    if len(table) < 2:
        return NominalRates()

    header = table[0]
    # Find most recent data row (row 1)
    data_row = table[1]

    for col_idx, cell in enumerate(header):
        cell_lower = cell.lower().strip()
        for prefix, field in _RD_LOAN_TYPE_MAP.items():
            if cell_lower.startswith(prefix) and (
                len(cell_lower) == len(prefix) or not cell_lower[len(prefix)].isdigit()
            ):
                if col_idx < len(data_row):
                    value = _parse_danish_decimal(data_row[col_idx])
                    if value is not None:
                        fraction = value / Decimal(100)
                        setattr(rates, field, fraction)
                break
    return rates


# ─── Fetcher: Nordea bond prices ────────────────────────────────────


def _fetch_nordea_bond_prices() -> NordeaBondPrices:
    """GET nordea.dk — parse HTML for fixed-rate bond kurs + coupon."""
    html_text = _http_get(NORDEA_URL)
    return _parse_nordea_bond_prices(html_text)


def _parse_nordea_bond_prices(html_text: str) -> NordeaBondPrices:
    """Parse Nordea HTML tables for bond prices.

    Table 0: obligationslån (fixed-rate).  Find row with '30' in label,
    excluding 'Frihed' variants.  Extract Kuponrente and Kurs.
    Table 2: flexlån.  Find rows labeled 'F3' and 'F5' for kontantrente.
    """
    parser = _SimpleTableParser()
    parser.feed(html_text)
    tables = parser.tables

    result = NordeaBondPrices()

    # Table 0: obligationslån
    if len(tables) > 0:
        table = tables[0]
        if len(table) > 0:
            header = table[0]
            kurs_idx = _find_col(header, "Kurs")
            coupon_idx = _find_col(header, "Kuponrente")
            if kurs_idx is not None and coupon_idx is not None:
                for row in table[1:]:
                    label = row[0].lower() if row else ""
                    if "30" in label and "frihed" not in label:
                        if kurs_idx < len(row):
                            result.fixed_kurs = _parse_danish_decimal(row[kurs_idx])
                        if coupon_idx < len(row):
                            result.fixed_coupon = _parse_danish_decimal(row[coupon_idx])
                        break

    # Table 2: flexlån
    if len(tables) > 2:
        table = tables[2]
        if len(table) > 0:
            header = table[0]
            kontant_idx = _find_col(header, "Kontantrente")
            if kontant_idx is not None:
                for row in table[1:]:
                    label = row[0].strip().lower() if row else ""
                    if label == "f3" and kontant_idx < len(row):
                        result.f3_kontantrente = _parse_danish_decimal(row[kontant_idx])
                    elif label == "f5" and kontant_idx < len(row):
                        result.f5_kontantrente = _parse_danish_decimal(row[kontant_idx])

    # Convert percentages to fractions
    if result.fixed_coupon is not None:
        result.fixed_coupon = result.fixed_coupon / Decimal(100)
    if result.f3_kontantrente is not None:
        result.f3_kontantrente = result.f3_kontantrente / Decimal(100)
    if result.f5_kontantrente is not None:
        result.f5_kontantrente = result.f5_kontantrente / Decimal(100)
    # fixed_kurs stays as-is (e.g. 93.48 — not a percentage)

    return result


def _find_col(header: list[str], name: str) -> int | None:
    """Find a column index by header name (case-insensitive, whitespace-tolerant)."""
    target = name.lower()
    for i, h in enumerate(header):
        if target in h.lower().replace("\n", " ").strip():
            return i
    return None


# ─── Fetcher: Finans Danmark obligationsrente ─────────────────────────


def _fetch_finansdanmark_obligationsrente() -> Decimal:
    """Scrape finansdanmark.dk for XLSX URL, download, parse lang obligationsrente.

    Returns lang obligationsrente as a fraction (e.g. 0.0449).
    """
    html_text = _http_get(FINANSDANMARK_URL)
    xlsx_url = _parse_finansdanmark_xlsx_url(html_text)
    if xlsx_url is None:
        raise ValueError("Could not find XLSX link on Finans Danmark page")
    xlsx_bytes = _http_get_bytes(xlsx_url)
    return _parse_finansdanmark_xlsx(xlsx_bytes)


def _parse_finansdanmark_xlsx_url(html_text: str) -> str | None:
    """Extract the XLSX download URL from the Finans Danmark HTML page."""
    match = re.search(r'href="([^"]*obl-rente[^"]*\.xlsx)"', html_text, re.IGNORECASE)
    if match:
        url = match.group(1)
        if url.startswith("/"):
            url = "https://finansdanmark.dk" + url
        return url
    match = re.search(r'href="(https?://[^"]*\.xlsx)"', html_text, re.IGNORECASE)
    return match.group(1) if match else None


def _parse_finansdanmark_xlsx(xlsx_bytes: bytes) -> Decimal:
    """Parse Finans Danmark XLSX via stdlib zipfile + XML.

    Sheet2 has the effective rates matching the HTML display.
    Find the header row with 'Lang rente', read the first data row's value.
    """
    ns = {"": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as zf:
        # Read shared strings
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root:
                text = "".join(t.text or "" for t in si.iter())
                shared_strings.append(text)

        # Read sheet2 (effective rates)
        sheet_path = "xl/worksheets/sheet2.xml"
        if sheet_path not in zf.namelist():
            sheet_path = "xl/worksheets/sheet1.xml"
        root = ET.fromstring(zf.read(sheet_path))

        rows = root.findall(".//row", ns)
        # Find header row (contains 'Lang rente')
        lang_col_idx: int | None = None
        header_row_num: int | None = None
        for row in rows:
            cells = _parse_xlsx_row(row, shared_strings, ns)
            for i, val in enumerate(cells):
                if val and "lang rente" in val.lower():
                    lang_col_idx = i
                    header_row_num = int(row.get("r", "0"))
                    break
            if lang_col_idx is not None:
                break

        if lang_col_idx is None:
            raise ValueError("Could not find 'Lang rente' column in XLSX")

        # First data row after header
        for row in rows:
            row_num = int(row.get("r", "0"))
            if header_row_num is not None and row_num <= header_row_num:
                continue
            cells = _parse_xlsx_row(row, shared_strings, ns)
            if lang_col_idx < len(cells):
                val_str = cells[lang_col_idx]
                if val_str:
                    try:
                        return Decimal(val_str) / Decimal(100)
                    except InvalidOperation:
                        continue
        raise ValueError("No data rows found after header in XLSX")


def _parse_xlsx_row(
    row_elem: ET.Element,
    shared_strings: list[str],
    ns: dict[str, str],
) -> list[str]:
    """Parse one <row> element from an XLSX sheet XML."""
    cells: list[str] = []
    for c in row_elem.findall("c", ns):
        ref = c.get("r", "")
        col_letters = re.match(r"([A-Z]+)", ref)
        if col_letters:
            col_idx = _col_letters_to_idx(col_letters.group(1))
        else:
            col_idx = len(cells)
        t = c.get("t", "")
        v_elem = c.find("v", ns)
        is_elem = c.find("is", ns)
        if t == "s" and v_elem is not None and v_elem.text:
            val = shared_strings[int(v_elem.text)]
        elif t == "inlineStr" and is_elem is not None:
            val = "".join(e.text or "" for e in is_elem.iter())
        elif v_elem is not None and v_elem.text:
            val = v_elem.text
        else:
            val = ""
        while len(cells) <= col_idx:
            cells.append("")
        cells[col_idx] = val
    return cells


def _col_letters_to_idx(letters: str) -> int:
    """Convert Excel column letters (A, B, ..., AA) to 0-based index."""
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


# ─── Fetcher: Jyske Bank reference rates ─────────────────────────────


def _fetch_jyske_reference_rates() -> ReferenceRates:
    """GET jyskebank.dk referencerenter via urllib (Cloudflare blocks curl).

    Cloudflare may block this request with a 403 challenge.  In that case,
    returns ReferenceRates() with all None fields.
    """
    try:
        html_text = _http_get(JYSKE_URL)
    except (HTTPError, URLError, OSError):
        return ReferenceRates()
    return _parse_jyske_reference_rates(html_text)


def _parse_jyske_reference_rates(html_text: str) -> ReferenceRates:
    """Parse Jyske Bank HTML for CIBOR/CITA reference rates.

    Expects a table with columns: Produkt, Reference, Rate p.a., Gyldig.
    Maps Reference values (CIBOR 3M, CIBOR 6M, CITA 3M) to ReferenceRates fields.
    """
    parser = _SimpleTableParser()
    parser.feed(html_text)
    tables = parser.tables
    if not tables:
        return ReferenceRates()

    table = tables[0]
    if len(table) < 2:
        return ReferenceRates()

    header = table[0]
    ref_idx = _find_col(header, "Reference")
    rate_idx = _find_col(header, "Rate")

    if ref_idx is None or rate_idx is None:
        return ReferenceRates()

    rates = ReferenceRates()
    for row in table[1:]:
        if ref_idx < len(row) and rate_idx < len(row):
            ref_label = row[ref_idx].strip().lower()
            rate = _parse_danish_decimal(row[rate_idx])
            if rate is None:
                continue
            fraction = rate / Decimal(100)
            if "cibor" in ref_label and "3" in ref_label:
                rates.cibor_3m = fraction
            elif "cibor" in ref_label and "6" in ref_label:
                rates.cibor_6m = fraction
            elif "cita" in ref_label and "3" in ref_label:
                rates.cita_3m = fraction
    return rates


# ─── Fetcher: DST DESTR rate ─────────────────────────────────────────


def _fetch_destr_rate() -> Decimal:
    """POST api.statbank.dk DNRENTD — DESTR Referencerenti (daily)."""
    body = {
        "table": "DNRENTD",
        "format": "CSV",
        "variables": [
            {"code": "INSTRUMENT", "values": ["DESNAA"]},
            {"code": "LAND", "values": ["DK"]},
            {"code": "OPGOER", "values": ["E"]},
            {"code": "TID", "values": ["(5)"]},
        ],
    }
    csv_text = _http_post_json(DST_URL, body)
    return _parse_destr_rate(csv_text)


def _parse_destr_rate(csv_text: str) -> Decimal:
    """Parse DST DNRENTD CSV for DESTR rate.

    The (5) request returns up to 5 recent dates.  The last available date
    with a non-'..' value is the current DESTR rate.
    """
    lines = csv_text.strip().splitlines()
    for line in reversed(lines[1:]):  # skip header, search from most recent
        parts = line.split(";")
        if len(parts) < 5:
            continue
        value = _parse_danish_decimal(parts[4])
        if value is not None:
            return value / Decimal(100)
    raise ValueError("No valid DESTR rate found in DST response")


# ─── Cache ───────────────────────────────────────────────────────────

_CACHED_RATES: MarketRates | None = None


def _read_cache(source_name: str) -> dict | None:
    """Read a source's cache file.  Returns None if missing or corrupt."""
    path = CACHE_DIR / f"{source_name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(source_name: str, data: dict) -> None:
    """Write a source's cache file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{source_name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _is_expired(cached: dict, ttl_seconds: int) -> bool:
    """Check if a cache entry has expired."""
    fetched_str = cached.get("fetched_at")
    if not fetched_str:
        return True
    try:
        fetched_at = datetime.fromisoformat(fetched_str)
    except (ValueError, TypeError):
        return True
    now = datetime.now(UTC)
    age = (now - fetched_at).total_seconds()
    return age > ttl_seconds


# ─── Per-source fetch + cache ────────────────────────────────────────

_SOURCE_FETCHERS: dict[str, object] = {
    "dst": _fetch_dst_effective_rates,
    "ecb": _fetch_ecb_bank_rate,
    "mybanker": _fetch_mybanker_bidragssatser,
    "rd": _fetch_rd_nominal_rates,
    "nordea": _fetch_nordea_bond_prices,
    "finansdanmark": _fetch_finansdanmark_obligationsrente,
    "jyske_ref": _fetch_jyske_reference_rates,
    "destr": _fetch_destr_rate,
}


def _fetch_and_cache(source_name: str) -> tuple[object | None, str]:
    """Fetch a single source and write its cache.  Returns (result, status_msg)."""
    fetcher = _SOURCE_FETCHERS[source_name]
    try:
        result = fetcher()
    except (
        HTTPError,
        URLError,
        ValueError,
        KeyError,
        IndexError,
        RuntimeError,
        OSError,
    ) as exc:  # fetchers must not crash the aggregate
        return None, f"failed: {exc}"

    # An empty parse result (e.g. mybanker layout change returning []) is not
    # distinguishable from real data here — it gets cached with status "ok"
    # and blocks re-fetch for the full TTL.  Fix: make fetchers raise on empty
    # results so the failure path above applies instead.
    if isinstance(result, list) and result and hasattr(result[0], "model_dump"):
        data = [e.model_dump(mode="json") for e in result]
    elif hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    elif isinstance(result, Decimal):
        data = str(result)
    else:
        data = result

    cache_entry = {
        # ttl_seconds is informational only; _is_expired re-reads CACHE_TTL
        "ttl_seconds": CACHE_TTL[source_name],
        "data": data,
    }
    _write_cache(source_name, cache_entry)
    return result, "ok"


def _load_from_cache(source_name: str) -> tuple[object | None, str]:
    """Load a source's result from its cache file if not expired."""
    cached = _read_cache(source_name)
    if cached is None:
        return None, "no cache"
    if _is_expired(cached, CACHE_TTL.get(source_name, 0)):
        return None, "expired"
    data = cached.get("data")
    return _deserialize_cached(source_name, data), "cached"


def _deserialize_cached(source_name: str, data: object) -> object | None:
    """Reconstruct a model from cached JSON data."""
    if data is None:
        return None
    try:
        if source_name == "dst":
            return DSTRates.model_validate(data)
        elif source_name == "ecb":
            return Decimal(str(data))
        elif source_name == "mybanker":
            return [BidragssatsEntry.model_validate(e) for e in data]
        elif source_name == "rd":
            return NominalRates.model_validate(data)
        elif source_name == "nordea":
            return NordeaBondPrices.model_validate(data)
        elif source_name == "finansdanmark":
            return Decimal(str(data))
        elif source_name == "jyske_ref":
            return ReferenceRates.model_validate(data)
        elif source_name == "destr":
            return Decimal(str(data))
    except (ValueError, KeyError, TypeError):
        return None
    return None


# ─── Public accessors ────────────────────────────────────────────────


def get_market_rates(force_refresh: bool = False) -> MarketRates:
    """Return cached MarketRates (full snapshot), or fetch all sources if expired.

    On fetch failure, returns last-good cached value for that source.
    Never raises — partial data is returned with failures recorded in sources.
    """
    global _CACHED_RATES

    if not force_refresh and _CACHED_RATES is not None:
        # Check if the in-memory cache is still fresh (use shortest TTL)
        min_ttl = min(CACHE_TTL.values())
        age = (datetime.now(UTC) - _CACHED_RATES.fetched_at).total_seconds()
        if age <= min_ttl:
            return _CACHED_RATES

    now = datetime.now(UTC)
    rates = MarketRates(fetched_at=now)
    sources: dict[str, str] = {}

    for source_name in _SOURCE_FETCHERS:
        result = None
        status = ""

        if force_refresh:
            result, status = _fetch_and_cache(source_name)
        else:
            result, status = _load_from_cache(source_name)
            if result is None:
                result, status = _fetch_and_cache(source_name)

        if result is not None:
            _apply_source(rates, source_name, result)
            sources[source_name] = status
        else:
            # Try last-good cache even if expired
            cached = _read_cache(source_name)
            if cached and cached.get("data") is not None:
                old_result = _deserialize_cached(source_name, cached["data"])
                if old_result is not None:
                    _apply_source(rates, source_name, old_result)
                    sources[source_name] = f"stale cache ({status})"
                else:
                    sources[source_name] = status
            else:
                sources[source_name] = status

    rates.sources = sources
    _CACHED_RATES = rates
    return rates


def _apply_source(rates: MarketRates, source_name: str, result: object) -> None:
    """Apply a fetcher result to the MarketRates snapshot."""
    if source_name == "dst":
        rates.dst_rates = result  # type: ignore[assignment]
    elif source_name == "ecb":
        rates.bank_rate = result  # type: ignore[assignment]
    elif source_name == "mybanker":
        rates.bidragssatser = result  # type: ignore[assignment]
    elif source_name == "rd":
        rates.nominal_rates = result  # type: ignore[assignment]
    elif source_name == "nordea":
        nordea = result
        if nordea.fixed_coupon is not None:
            rates.fixed_coupon_rate = nordea.fixed_coupon
        if nordea.fixed_kurs is not None:
            rates.fixed_bond_price = nordea.fixed_kurs
    elif source_name == "finansdanmark":
        rates.lang_obligationsrente = result  # type: ignore[assignment]
    elif source_name == "jyske_ref":
        jyske = result
        if jyske.cibor_3m is not None:
            rates.reference_rates.cibor_3m = jyske.cibor_3m
        if jyske.cibor_6m is not None:
            rates.reference_rates.cibor_6m = jyske.cibor_6m
        if jyske.cita_3m is not None:
            rates.reference_rates.cita_3m = jyske.cita_3m
    elif source_name == "destr":
        rates.reference_rates.destr = result  # type: ignore[assignment]


def get_nominal_rate(loan_type: LoanType) -> Decimal | None:
    """Return cached nominal rate for a single loan type.  Reads only RD.dk cache."""
    if loan_type == LoanType.FIXED:
        return None
    cached = _read_cache("rd")
    if cached is None:
        return None
    nom = _deserialize_cached("rd", cached.get("data"))
    if nom is None:
        return None
    field = loan_type.value  # 'f1', 'f3', 'f5'
    return getattr(nom, field, None)


def get_reference_rate(rate_type: str) -> Decimal | None:
    """Return cached reference rate (cibor_3m, cibor_6m, cita_3m, destr)."""
    valid = {"cibor_3m", "cibor_6m", "cita_3m", "destr"}
    if rate_type not in valid:
        return None

    if rate_type == "destr":
        cached = _read_cache("destr")
        if cached is None:
            return None
        return _deserialize_cached("destr", cached.get("data"))

    cached = _read_cache("jyske_ref")
    if cached is None:
        return None
    refs = _deserialize_cached("jyske_ref", cached.get("data"))
    if refs is None:
        return None
    return getattr(refs, rate_type, None)


def get_bidragssatser(
    institute: str | None = None,
    loan_type: str | None = None,
) -> list[BidragssatsEntry]:
    """Return cached bidragssatser, optionally filtered."""
    cached = _read_cache("mybanker")
    if cached is None:
        return []
    entries = _deserialize_cached("mybanker", cached.get("data"))
    if entries is None:
        return []
    result = entries
    if institute is not None:
        result = [e for e in result if e.institute.value == institute]
    if loan_type is not None:
        result = [e for e in result if e.loan_type.value == loan_type]
    return result


def get_bond_prices() -> dict:
    """Return cached fixed-rate bond prices."""
    cached = _read_cache("nordea")
    if cached is None:
        return {
            "fixed_coupon": None,
            "fixed_kurs": None,
            "f3_kontantrente": None,
            "f5_kontantrente": None,
            "fetched_at": None,
            "source": "nordea.dk",
        }
    nordea = _deserialize_cached("nordea", cached.get("data"))
    if nordea is None:
        return {
            "fixed_coupon": None,
            "fixed_kurs": None,
            "f3_kontantrente": None,
            "f5_kontantrente": None,
            "fetched_at": cached.get("fetched_at"),
            "source": "nordea.dk",
        }
    return {
        "fixed_coupon": str(nordea.fixed_coupon)
        if nordea.fixed_coupon is not None
        else None,
        "fixed_kurs": str(nordea.fixed_kurs) if nordea.fixed_kurs is not None else None,
        "f3_kontantrente": str(nordea.f3_kontantrente)
        if nordea.f3_kontantrente is not None
        else None,
        "f5_kontantrente": str(nordea.f5_kontantrente)
        if nordea.f5_kontantrente is not None
        else None,
        "fetched_at": cached.get("fetched_at"),
        "source": "nordea.dk",
    }


def refresh_market_rates() -> MarketRates:
    """Force refresh all sources.  Used by /api/market-rates/refresh."""
    return get_market_rates(force_refresh=True)


# ─── Bidragssats lookup ──────────────────────────────────────────────


def lookup_bidragssats(key: BidragssatsKey, rates: MarketRates) -> Decimal:
    """Look up bidragssats from cached MarketRates.

    Fallback chain: (1) mybanker per-LTV entry, (2) DST avg bidrag for
    loan type, (3) hardcoded 0.006.
    """
    # (1) Search mybanker entries
    for entry in rates.bidragssatser:
        if (
            entry.institute == key.institute
            and entry.loan_type == key.loan_type
            and entry.ltv_band == key.ltv_band
            and entry.afdragsfrihed == key.afdragsfrihed
        ):
            return entry.bidragssats

    # (2) Fallback to DST avg bidrag for this loan type
    if rates.dst_rates is not None:
        if key.loan_type == LoanType.F3 and rates.dst_rates.f3_bidrag_avg is not None:
            return rates.dst_rates.f3_bidrag_avg
        if key.loan_type == LoanType.F5 and rates.dst_rates.f5_bidrag_avg is not None:
            return rates.dst_rates.f5_bidrag_avg
        if (
            key.loan_type == LoanType.FIXED
            and rates.dst_rates.fixed_bidrag_avg is not None
        ):
            return rates.dst_rates.fixed_bidrag_avg

    # (3) Hardcoded default
    return _DEFAULT_BIDRAGSSATS


# ─── Adapter: build_preset_from_market ────────────────────────────────


def build_preset_from_market(
    rates: MarketRates,
    preset_name: str = "default",
    institute: Institute = Institute.NYKREDIT,
    ltv_band: LTVBand = LTVBand.ZERO_TO_40,
) -> CalculatorInput:
    """Build a CalculatorInput using live market data instead of hardcoded values.

    Preserves preset structure (same alternatives, same bank share, same maturity).
    Replaces: rates, bidragssatser, bond prices, bank rate.
    Lives in market_data.py (adapter), not engine.py (deep module).
    """
    if preset_name not in PRESETS:
        raise ValueError(f"Unknown preset '{preset_name}'")

    preset = PRESETS[preset_name].model_copy(deep=True)

    for alt in preset.alternatives:
        for comp in alt.components:
            if comp.loan_type == LoanType.FIXED:
                # Fixed-rate: override coupon rate from Nordea, fall back to
                # Finans Danmark lang obligationsrente as a proxy
                if rates.fixed_coupon_rate is not None:
                    comp.rate = max(Decimal(0), rates.fixed_coupon_rate)
                elif rates.lang_obligationsrente is not None:
                    comp.rate = max(Decimal(0), rates.lang_obligationsrente)
                # Bond price (kurs) from Nordea only
                if rates.fixed_bond_price is not None:
                    comp.price = rates.fixed_bond_price

            elif comp.loan_type in (LoanType.F1, LoanType.F3, LoanType.F5):
                # Flexlån: override nominal rate from RD.dk
                nominal = getattr(rates.nominal_rates, comp.loan_type.value, None)
                if nominal is not None:
                    comp.rate = max(Decimal(0), nominal)

            elif comp.loan_type in (LoanType.CITA, LoanType.CIBOR, LoanType.DESTR):
                # Reference-rate loans: override reference_rate
                ref_field = {
                    LoanType.CITA: "cita_3m",
                    LoanType.CIBOR: "cibor_3m",
                    LoanType.DESTR: "destr",
                }.get(comp.loan_type)
                if ref_field is not None:
                    ref_val = getattr(rates.reference_rates, ref_field, None)
                    if ref_val is not None:
                        comp.reference_rate = max(Decimal(0), ref_val)
                        # Re-derive rate = reference_rate + margin
                        if comp.margin is not None:
                            comp.rate = comp.reference_rate + comp.margin

            elif comp.loan_type == LoanType.T:
                # T-lån: uses underlying flexlån rate (typically F5)
                nominal = rates.nominal_rates.f5
                if nominal is not None:
                    comp.rate = max(Decimal(0), nominal)

            # Override bidragssats for realkredit components
            if comp.component.value == "realkredit":
                afdragsfrihed = comp.interest_only_years > 0
                key = BidragssatsKey(
                    institute=institute,
                    loan_type=comp.loan_type,
                    ltv_band=ltv_band,
                    afdragsfrihed=afdragsfrihed,
                )
                comp.bidragssats = lookup_bidragssats(key, rates)

            # Override bank rate for bank components
            if rates.bank_rate is not None and comp.component.value == "bank":
                comp.rate = rates.bank_rate

    return preset


# ─── Simple table parser (shared by RD, Nordea, Jyske) ───────────────


class _SimpleTableParser(HTMLParser):
    """Parse all <table> elements into a list of list-of-list-str.

    Each table is a list of rows, each row is a list of cell text strings.
    Handles colspan by repeating the cell value across spanned columns.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] = []
        self._in_cell = False
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table":
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._in_cell = True
            self._cur_cell = []
            cs = attr_dict.get("colspan", "1")
            self._colspan = int(cs) if cs and cs.isdigit() else 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._cur_table is not None:
            self.tables.append(self._cur_table)
            self._cur_table = None
        elif tag == "tr" and self._cur_row is not None and self._cur_table is not None:
            self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag in ("td", "th") and self._in_cell:
            text = "".join(self._cur_cell).strip()
            text = re.sub(r"\s+", " ", text)
            for _ in range(self._colspan):
                self._cur_row.append(text)
            self._in_cell = False
            self._cur_cell = []
            self._colspan = 1

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cur_cell.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._in_cell:
            self._cur_cell.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._in_cell:
            try:
                code = int(name)
                self._cur_cell.append(chr(code))
            except (ValueError, OverflowError):
                pass
