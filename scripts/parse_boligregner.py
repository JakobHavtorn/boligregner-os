#!/usr/bin/env python3
"""Parse a boligregner.dk result page set into structured reference data.

Usage:
    uv run python scripts/parse_boligregner.py <result_url_base>
    uv run python scripts/parse_boligregner.py http://boligregner.dk/resultater/hent/4cea022e-e606-4c95-877c-1d583d27df20/

The script fetches subpages 0..3, extracts:
  - Loan overview (rente, kurs, bidragssats, hovedstol, issue costs, etc.)
  - 5-year horizon scenarios (rente+bidrag, afdrag, ydelse, restgæld)
  - Amortization schedule (quarterly breakdown)

Outputs JSON to stdout. Pipe to jq or redirect to a file.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from decimal import Decimal
from html import unescape
from typing import Any


def _clean(text: str) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return " ".join(text.split()).strip()


def _parse_danish_number(text: str) -> str | None:
    """Parse a Danish-formatted number (1.234,56) into a Decimal string."""
    text = _clean(text)
    if not text or text in ("&nbsp;", "-"):
        return None
    text = text.replace(".", "").replace(",", ".")
    # Strip trailing % or other non-numeric suffixes
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text or text == "-":
        return None
    return text


def _parse_percent(text: str) -> str | None:
    """Parse a Danish-formatted percentage (3,26%) into a Decimal rate (0.0326)."""
    text = _clean(text)
    text = text.replace("%", "").replace(",", ".").strip()
    if not text or text == "-":
        return None
    return str(Decimal(text) / Decimal(100))


def _parse_date(text: str) -> str | None:
    """Parse a Danish date (09-10-2026) into ISO format (2026-10-09)."""
    text = _clean(text)
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", text)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def _fetch(url: str) -> str:
    """Fetch a URL, following redirects."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_tables(html: str) -> list[list[list[str]]]:
    """Extract all tables as a list of (list of row dicts with cell values)."""
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL)
    result = []
    for table_html in tables:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)
        table_rows = []
        for row_html in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL)
            cells_clean = [_clean(c) for c in cells]
            if any(cells_clean):
                table_rows.append(cells_clean)
        if table_rows:
            result.append(table_rows)
    return result


def _find_table_by_label(
    tables: list[list[list[str]]], label: str
) -> list[list[str]] | None:
    """Find the first table containing a row whose first cell matches label."""
    for table in tables:
        for row in table:
            if row and label.lower() in row[0].lower():
                return table
    return None


def _get_value(tables: list[list[list[str]]], label: str) -> str | None:
    """Get the value for a row label across all tables (first match)."""
    for table in tables:
        for row in table:
            if row and label.lower() in row[0].lower():
                if len(row) >= 2:
                    return row[1]
    return None


def parse_overview(tables: list[list[list[str]]]) -> dict[str, Any]:
    """Parse the loan overview table (lånetype, rente, kurs, etc.)."""
    overview: dict[str, Any] = {}

    # Find the overview table - it has "Lånetype:" in first column
    ov_table = _find_table_by_label(tables, "Lånetype:")
    if not ov_table:
        return overview

    for row in ov_table:
        if len(row) < 2:
            continue
        key = row[0].rstrip(":").strip()
        val = row[1].strip()
        if not val or val == "&nbsp;":
            continue
        key_norm = key.lower()
        if "lånetype" in key_norm:
            overview["loan_type_raw"] = val
            if "F3" in val:
                overview["loan_type"] = "F3"
            elif "F5" in val:
                overview["loan_type"] = "F5"
            elif "F1" in val:
                overview["loan_type"] = "F1"
            elif "fast" in val.lower() or "obligationsl" in val.lower():
                overview["loan_type"] = "FIXED"
        elif "løbetid" in key_norm:
            overview["maturity_raw"] = val
            m = re.search(r"(\d+)\s*år", val)
            if m:
                overview["maturity_years"] = int(m.group(1))
        elif "rente" in key_norm and "bidrag" not in key_norm:
            overview["rate"] = _parse_percent(val)
        elif "bidragssats" in key_norm:
            overview["bidragssats"] = _parse_percent(val)
        elif "hovedstol" in key_norm and "obligations" not in key_norm:
            overview["hovedstol"] = _parse_danish_number(val)
        elif "obligationshovedstol" in key_norm:
            overview["obligationshovedstol"] = _parse_danish_number(val)
        elif "optagelseskurs" in key_norm:
            overview["price"] = _parse_danish_number(val)
        elif "kursværdi" in key_norm or "kursvaerdi" in key_norm:
            overview["kursvaerdi"] = _parse_danish_number(val)
        elif "etableringsomk" in key_norm and "samlet" not in key_norm:
            overview["issue_costs_nominal"] = _parse_danish_number(val)
        elif "faktisk provenu" in key_norm:
            overview["faktisk_provenu"] = _parse_danish_number(val)
        elif "åop" in key_norm and "skat" not in key_norm:
            overview["aop_before_tax"] = _parse_percent(val)
        elif "åop" in key_norm and "skat" in key_norm:
            overview["aop_after_tax"] = _parse_percent(val)

    return overview


def parse_input(tables: list[list[list[str]]]) -> dict[str, Any]:
    """Parse the input table (ønsket provenu, etc.)."""
    inp: dict[str, Any] = {}
    val = _get_value(tables, "Ønsket provenu")
    if val:
        inp["desired_provenu"] = _parse_danish_number(val)
    return inp


def _parse_horizon_table(table: list[list[str]]) -> dict[str, Any]:
    """Parse a single horizon scenario table into a dict."""
    horizon: dict[str, Any] = {}
    scenarios = ["rentefall", "unchanged", "renterise"]

    for row in table:
        if not row or len(row) < 4:
            continue
        label = row[0].lower()
        if "rente og bidrag" in label:
            horizon["rente_bidrag"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }
        elif "afdrag" in label and "ydelse" not in label:
            horizon["afdrag"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }
        elif "ydelse" in label and "perioden" in label and "skat" in label:
            horizon["ydelse_total"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }
        elif (
            "ydelse" in label
            and "måned" in label
            and "startdato" in label
            and "skat" in label
        ):
            horizon["ydelse_monthly_after_tax"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }
        elif (
            "restgæld" in label
            or "restgaeld" in label
            or "kontantlånsrestgæld" in label
            or "obligationsrestgæld" in label
        ):
            horizon["restgaeld"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }
        elif "periodeomkost" in label:
            horizon["periodeomkostninger"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }
        elif "indfrielseskurs" in label:
            horizon["indfrielseskurs"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }
        elif "indfrielsesbeløb" in label or "indfrielsesbeloeb" in label:
            horizon["indfrielsesbeloeb"] = {
                s: _parse_danish_number(row[i + 1]) for i, s in enumerate(scenarios)
            }

    return horizon


def parse_horizon(tables: list[list[list[str]]]) -> dict[str, Any]:
    """Parse all 5-year horizon scenario tables.

    boligregner.dk shows two horizon tables per subpage:
    1. Combined (realkredit + bank) — uses "Samlet ydelse" and "Restgæld"
    2. Realkredit-only — uses "Ydelse i perioden" (no "Samlet") and
       "Kontantlånsrestgæld" (flex) or "Obligationsrestgæld" (fixed)

    Returns:
        {"combined": {...}, "realkredit": {...}}
    """
    result: dict[str, Any] = {"combined": {}, "realkredit": {}}
    found = 0

    for table in tables:
        header_text = " ".join(table[0]) if table else ""
        if "Rentefald" not in header_text:
            continue

        # Distinguish: combined table has "Samlet ydelse i perioden",
        # realkredit-only has "Ydelse i perioden" (without "Samlet").
        labels = [row[0].lower() for row in table if row]
        is_combined = any("samlet ydelse" in l for l in labels)

        parsed = _parse_horizon_table(table)
        if is_combined:
            result["combined"] = parsed
        else:
            result["realkredit"] = parsed
        found += 1

    return result


def parse_dates(tables: list[list[list[str]]]) -> dict[str, str | None]:
    """Parse startdato and horisontdato."""
    dates: dict[str, str | None] = {}
    val = _get_value(tables, "Startdato")
    if val:
        dates["start_date"] = _parse_date(val)
    val = _get_value(tables, "Horisontdato")
    if val:
        dates["horizon_date"] = _parse_date(val)
    return dates


def parse_amortization(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    """Parse the amortization schedule table (quarterly payments)."""
    schedule: list[dict[str, Any]] = []
    for table in tables:
        header_text = " ".join(table[0]) if table else ""
        if "Restgæld efter afdrag" not in header_text:
            continue
        for row in table[1:]:  # Skip header
            if not row or len(row) < 6:
                continue
            period = row[0]
            if not period or period.startswith("&nbsp"):
                continue
            # Skip annual summary rows (just year, no month)
            if re.match(r"^\d{4}$", period):
                continue
            schedule.append(
                {
                    "period": period,
                    "restgaeld": _parse_danish_number(row[1]),
                    "afdrag": _parse_danish_number(row[2]),
                    "rente": _parse_danish_number(row[3]),
                    "bidrag": _parse_danish_number(row[4]),
                    "ydelse": _parse_danish_number(row[5]),
                }
            )
        break
    return schedule


def parse_subpage(url: str) -> dict[str, Any]:
    """Fetch and parse a single subpage."""
    html = _fetch(url)
    tables = _extract_tables(html)

    return {
        "input": parse_input(tables),
        "overview": parse_overview(tables),
        "dates": parse_dates(tables),
        "horizon": parse_horizon(tables),
        "amortization": parse_amortization(tables),
    }


def parse_result_set(url_base: str) -> dict[str, Any]:
    """Fetch and parse subpages 0..3 from a boligregner.dk result URL.

    Page 0: Summary (comparison of all alternatives)
    Pages 1-3: Individual loan details
    """
    if not url_base.endswith("/"):
        url_base += "/"

    result: dict[str, Any] = {
        "source_url": url_base,
        "pages": {},
    }

    for i in range(4):
        url = f"{url_base}{i}/"
        try:
            parsed = parse_subpage(url)
            result["pages"][i] = parsed
            ov = parsed.get("overview", {})
            loan_type = ov.get("loan_type", "unknown")
            print(
                f"  Page {i}: {loan_type} — rente={ov.get('rate', '?')}, "
                f"kurs={ov.get('price', '?')}, bidrag={ov.get('bidragssats', '?')}",
                file=sys.stderr,
            )
        except Exception as e:
            result["pages"][i] = {"error": str(e)}
            print(f"  Page {i}: ERROR — {e}", file=sys.stderr)

    # Build a flat summary of the reference values we care about
    result["summary"] = _build_summary(result["pages"])
    return result


def _build_summary(pages: dict[int, Any]) -> list[dict[str, Any]]:
    """Build a flat summary list of reference values per loan alternative."""
    summary = []
    for i in range(1, 4):  # Pages 1-3 are the loan alternatives
        page = pages.get(i, {})
        if "error" in page:
            continue
        ov = page.get("overview", {})
        inp = page.get("input", {})
        dates = page.get("dates", {})
        hz = page.get("horizon", {})

        if not ov:
            continue

        entry: dict[str, Any] = {
            "page_index": i,
            "loan_type": ov.get("loan_type"),
            "loan_type_raw": ov.get("loan_type_raw"),
            "rate": ov.get("rate"),
            "price": ov.get("price"),
            "maturity_years": ov.get("maturity_years"),
            "bidragssats": ov.get("bidragssats"),
            "hovedstol": ov.get("hovedstol"),
            "obligationshovedstol": ov.get("obligationshovedstol"),
            "kursvaerdi": ov.get("kursvaerdi"),
            "issue_costs_nominal": ov.get("issue_costs_nominal"),
            "desired_provenu": inp.get("desired_provenu"),
            "faktisk_provenu": ov.get("faktisk_provenu"),
            "aop_before_tax": ov.get("aop_before_tax"),
            "aop_after_tax": ov.get("aop_after_tax"),
            "start_date": dates.get("start_date"),
            "horizon_date": dates.get("horizon_date"),
        }

        # Extract both horizon views
        hz_combined = hz.get("combined", {})
        hz_realkredit = hz.get("realkredit", {})

        # Use combined horizon as the primary reference (matches our engine which
        # computes combined realkredit + bank scenarios).
        # Also include realkredit-only for component-level comparison.
        for prefix, hz_data in [
            ("combined", hz_combined),
            ("realkredit", hz_realkredit),
        ]:
            if not hz_data:
                continue
            p = f"{prefix}_" if prefix != "combined" else ""
            entry[f"{p}horizon_rente_bidrag"] = hz_data.get("rente_bidrag", {}).get(
                "unchanged"
            )
            entry[f"{p}horizon_afdrag"] = hz_data.get("afdrag", {}).get("unchanged")
            entry[f"{p}horizon_ydelse_total"] = hz_data.get("ydelse_total", {}).get(
                "unchanged"
            )
            entry[f"{p}horizon_restgaeld"] = hz_data.get("restgaeld", {}).get(
                "unchanged"
            )
            entry[f"{p}horizon_periodeomkostninger"] = hz_data.get(
                "periodeomkostninger", {}
            ).get("unchanged")
            # Also include shock scenarios
            for scenario in ("rentefall", "renterise"):
                for field in ("rente_bidrag", "afdrag", "ydelse_total", "restgaeld"):
                    key = f"{p}horizon_{field}_{scenario}"
                    if field in hz_data and scenario in hz_data[field]:
                        entry[key] = hz_data[field][scenario]

        summary.append(entry)

    return summary


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: parse_boligregner.py <result_url_base>\n"
            "Example: parse_boligregner.py "
            "http://boligregner.dk/resultater/hent/4cea022e-.../",
            file=sys.stderr,
        )
        sys.exit(1)

    url_base = sys.argv[1]
    print(f"Parsing boligregner.dk result set: {url_base}", file=sys.stderr)

    result = parse_result_set(url_base)

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
