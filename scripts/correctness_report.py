#!/usr/bin/env python3
"""Correctness report: engine output vs boligregner.dk reference data.

Produces an aligned deviation table across all reference cases. Complements
the unit tests (which assert pass/fail within tolerances) by showing the
magnitude of each deviation — useful when iterating on correctness gaps.

Usage:
    uv run python scripts/correctness_report.py

The script imports REFERENCE_CASES from tests/test_engine.py, so new reference
sessions added there are automatically included in the report.

Reference data was captured from boligregner.dk on Oct 5–8, 2026.
See docs/2026-10-07-boligregner-reference-data.md for provenance.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

# Add src/ and repo root to sys.path so we can import the engine and test data.
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))
sys.path.insert(0, str(_repo_root))

from boligregner.engine import calculate  # noqa: E402
from boligregner.models import LoanType  # noqa: E402
from tests.test_engine import (  # noqa: E402
    REFERENCE_CASES,
    _effective_aap,
    _horizon_row,
    _monthly_equiv_ydelse,
)

# ── Formatting helpers ──────────────────────────────────────────────


def _money(v: Decimal) -> str:
    return f"{v:,.0f}"


def _pct(v: Decimal) -> str:
    return f"{float(v) * 100:.4f}%"


def _kurs(v: Decimal) -> str:
    return f"{float(v):.2f}"


def _abs_money(actual: Decimal, expected: Decimal) -> str:
    """Absolute diff in DKK."""
    diff = int(actual - expected)
    return f"{diff:+,}"


def _abs_pp(actual: Decimal, expected: Decimal) -> str:
    """Absolute diff in pp. Inputs are fractions (0.0448 = 4.48%)."""
    diff = (float(actual) - float(expected)) * 100
    return f"{diff:+.4f}pp"


def _abs_kurs(actual: Decimal, expected: Decimal) -> str:
    """Absolute diff in pp for kurs values already on a 0-100 scale."""
    diff = float(actual) - float(expected)
    return f"{diff:+.2f}pp"


def _rel_diff(actual: Decimal, expected: Decimal) -> str:
    """Relative diff in percent, always."""
    if expected == 0:
        return "n/a"
    diff = (float(actual) - float(expected)) / float(expected) * 100
    return f"{diff:+.2f}%"


# ── Row collection ──────────────────────────────────────────────────

# Each row: (case_id, metric, engine_str, ref_str, abs_diff_str, rel_diff_str)
Row = tuple[str, str, str, str, str, str]


def _case_summary_rows(case: dict) -> list[Row]:
    """One descriptive row per case for the summary table."""
    inp = case["input"]
    alt = inp.alternatives[0]
    comps = alt.components
    loan_type = comps[0].loan_type
    n_comps = len(comps)

    type_label = loan_type.value if isinstance(loan_type, LoanType) else str(loan_type)
    if n_comps > 1:
        type_label = f"{type_label}+bank"

    rate_str = f"{float(comps[0].rate) * 100:.2f}%"
    price_str = f"{float(comps[0].price):.2f}" if comps[0].price != 100 else "par"
    provenu_str = f"{int(inp.desired_provenu):,}"
    maturity_str = f"{comps[0].maturity_years}y"

    return [
        (
            case["id"],
            type_label,
            rate_str,
            price_str,
            provenu_str,
            maturity_str,
        )
    ]


def _rows_for_case(case: dict) -> list[Row]:
    """Collect all metric rows for a single reference case."""
    result = calculate(case["input"])
    alt = result.alternatives[0]
    exp = case["expected"]
    cid = case["id"]
    rows: list[Row] = []

    if "hovedstol" in exp:
        actual = alt.total_hovedstol
        expected = exp["hovedstol"]
        rows.append(
            (
                cid,
                "Hovedstol",
                _money(actual),
                _money(expected),
                _abs_money(actual, expected),
                _rel_diff(actual, expected),
            )
        )

    if "ydelse" in exp:
        actual = _monthly_equiv_ydelse(alt, case["component_ppys"])
        expected = exp["ydelse"]
        rows.append(
            (
                cid,
                "Ydelse",
                _money(actual),
                _money(expected),
                _abs_money(actual, expected),
                _rel_diff(actual, expected),
            )
        )

    if "aap" in exp:
        actual = _effective_aap(alt, case["aap_ppy"])
        expected = exp["aap"]
        rows.append(
            (
                cid,
                "ÅOP",
                _pct(actual),
                _pct(expected),
                _abs_pp(actual, expected),
                _rel_diff(actual, expected),
            )
        )

    h0 = exp.get("horizon_0pct")
    if h0:
        row = _horizon_row(result, 0, Decimal(0))
        if "rente" in h0:
            rows.append(
                (
                    cid,
                    "Horizon rente",
                    _money(row.rente_total),
                    _money(h0["rente"]),
                    _abs_money(row.rente_total, h0["rente"]),
                    _rel_diff(row.rente_total, h0["rente"]),
                )
            )
        if "afdrag" in h0:
            rows.append(
                (
                    cid,
                    "Horizon afdrag",
                    _money(row.afdrag_total),
                    _money(h0["afdrag"]),
                    _abs_money(row.afdrag_total, h0["afdrag"]),
                    _rel_diff(row.afdrag_total, h0["afdrag"]),
                )
            )
        if "ydelse" in h0:
            rows.append(
                (
                    cid,
                    "Horizon ydelse",
                    _money(row.ydelse_total),
                    _money(h0["ydelse"]),
                    _abs_money(row.ydelse_total, h0["ydelse"]),
                    _rel_diff(row.ydelse_total, h0["ydelse"]),
                )
            )
        if "restgaeld" in h0:
            rows.append(
                (
                    cid,
                    "Horizon restgæld",
                    _money(row.restgaeld),
                    _money(h0["restgaeld"]),
                    _abs_money(row.restgaeld, h0["restgaeld"]),
                    _rel_diff(row.restgaeld, h0["restgaeld"]),
                )
            )

    gns_refs = exp.get("horizon_gns_kurs")
    if gns_refs:
        for shock, expected_kurs in gns_refs.items():
            row = _horizon_row(result, 0, shock)
            actual_kurs = row.gns_kurs
            shock_label = f"{int(float(shock) * 100):+d}%"
            metric = f"Gns.kurs ({shock_label})"
            rows.append(
                (
                    cid,
                    metric,
                    _kurs(actual_kurs),
                    _kurs(expected_kurs),
                    _abs_kurs(actual_kurs, expected_kurs),
                    _rel_diff(actual_kurs, expected_kurs),
                )
            )

    return rows


def _print_table(rows: list[Row]) -> None:
    """Print rows as a column-aligned table."""
    headers = ("Case", "Metric", "Engine", "Reference", "Abs. diff", "Rel. diff")
    cols = list(zip(headers, *rows))
    widths = [max(len(str(c)) for c in col) for col in cols]

    def _fmt_row(values: tuple[str, ...]) -> str:
        parts = []
        for i, (val, w) in enumerate(zip(values, widths)):
            # Case and Metric: left-aligned. Engine, Reference, Diff: right-aligned.
            if i < 2:
                parts.append(str(val).ljust(w))
            else:
                parts.append(str(val).rjust(w))
        return "  ".join(parts)

    sep = "  ".join("-" * w for w in widths)

    print(_fmt_row(headers))
    print(sep)
    prev_case = None
    for row in rows:
        if prev_case is not None and row[0] != prev_case:
            print(sep)
        print(_fmt_row(row))
        prev_case = row[0]


def _print_summary_table(rows: list[Row]) -> None:
    """Print a simple aligned table with all columns left-aligned."""
    headers = ("Case", "Type", "Rate", "Price", "Provenu", "Maturity")
    cols = list(zip(headers, *rows))
    widths = [max(len(str(c)) for c in col) for col in cols]

    def _fmt_row(values: tuple[str, ...]) -> str:
        return "  ".join(str(v).ljust(w) for v, w in zip(values, widths))

    sep = "  ".join("-" * w for w in widths)

    print(_fmt_row(headers))
    print(sep)
    for row in rows:
        print(_fmt_row(row))


def main() -> None:
    print("boligregner-os correctness report")
    print(f"Reference data: boligregner.dk (captured Oct 5–8, 2026)")
    print(f"Cases: {len(REFERENCE_CASES)}")
    print(f"See docs/2026-10-07-boligregner-reference-data.md for provenance.")
    print()

    summary_rows: list[Row] = []
    for case in REFERENCE_CASES:
        summary_rows.extend(_case_summary_rows(case))

    _print_summary_table(summary_rows)
    print()

    all_rows: list[Row] = []
    for case in REFERENCE_CASES:
        all_rows.extend(_rows_for_case(case))

    _print_table(all_rows)
    print()


if __name__ == "__main__":
    main()
