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


def _pct_diff(actual: Decimal, expected: Decimal) -> str:
    if expected == 0:
        return "n/a"
    diff = (float(actual) - float(expected)) / float(expected) * 100
    return f"{diff:+.2f}%"


def _pp_diff(actual: Decimal, expected: Decimal) -> str:
    """Diff in percentage points. Inputs are fractions (0.0448 = 4.48%)."""
    diff = (float(actual) - float(expected)) * 100
    return f"{diff:+.4f}pp"


def _kurs_diff(actual: Decimal, expected: Decimal) -> str:
    """Diff for kurs values already on a 0-100 scale."""
    diff = float(actual) - float(expected)
    return f"{diff:+.2f}pp"


# ── Row collection ──────────────────────────────────────────────────

# Each row: (case_id, metric, engine_str, ref_str, diff_str)
Row = tuple[str, str, str, str, str]


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
                _pct_diff(actual, expected),
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
                _pct_diff(actual, expected),
            )
        )

    if "aap" in exp:
        actual = _effective_aap(alt, case["aap_ppy"])
        expected = exp["aap"]
        rows.append(
            (cid, "ÅOP", _pct(actual), _pct(expected), _pp_diff(actual, expected))
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
                    _pct_diff(row.rente_total, h0["rente"]),
                )
            )
        if "afdrag" in h0:
            rows.append(
                (
                    cid,
                    "Horizon afdrag",
                    _money(row.afdrag_total),
                    _money(h0["afdrag"]),
                    _pct_diff(row.afdrag_total, h0["afdrag"]),
                )
            )
        if "ydelse" in h0:
            rows.append(
                (
                    cid,
                    "Horizon ydelse",
                    _money(row.ydelse_total),
                    _money(h0["ydelse"]),
                    _pct_diff(row.ydelse_total, h0["ydelse"]),
                )
            )
        if "restgaeld" in h0:
            rows.append(
                (
                    cid,
                    "Horizon restgæld",
                    _money(row.restgaeld),
                    _money(h0["restgaeld"]),
                    _pct_diff(row.restgaeld, h0["restgaeld"]),
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
                    _kurs_diff(actual_kurs, expected_kurs),
                )
            )

    return rows


def _print_table(rows: list[Row]) -> None:
    """Print rows as a column-aligned table."""
    headers = ("Case", "Metric", "Engine", "Reference", "Diff")
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
    for row in rows:
        print(_fmt_row(row))


def main() -> None:
    print("boligregner-os correctness report")
    print(f"Reference data: boligregner.dk (captured Oct 5–8, 2026)")
    print(f"Cases: {len(REFERENCE_CASES)}")
    print(f"See docs/2026-10-07-boligregner-reference-data.md for provenance.")
    print()

    all_rows: list[Row] = []
    for case in REFERENCE_CASES:
        all_rows.extend(_rows_for_case(case))

    _print_table(all_rows)
    print()


if __name__ == "__main__":
    main()
