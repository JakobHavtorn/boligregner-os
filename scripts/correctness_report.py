#!/usr/bin/env python3
"""Correctness report: engine output vs boligregner.dk reference data.

Produces a per-case, per-metric deviation table. Complements the unit tests
(which assert pass/fail within tolerances) by showing the magnitude of each
deviation — useful when iterating on correctness gaps.

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


def _pct_diff(actual: Decimal, expected: Decimal) -> str:
    if expected == 0:
        return "n/a"
    diff = (float(actual) - float(expected)) / float(expected) * 100
    return f"{diff:+.2f}%"


def _pp_diff(actual: Decimal, expected: Decimal) -> str:
    """Diff in percentage points. Inputs are fractions (0.0448 = 4.48%)."""
    diff = (float(actual) - float(expected)) * 100
    return f"{diff:+.4f}pp"


def _fmt_money(v: Decimal) -> str:
    return f"{v:>14,.0f}"


def _fmt_pct(v: Decimal) -> str:
    return f"{float(v) * 100:>8.4f}%"


def _kurs_diff(actual: Decimal, expected: Decimal) -> str:
    """Diff for kurs values already on a 0-100 scale."""
    diff = float(actual) - float(expected)
    return f"{diff:+.2f}pp"


def report_case(case: dict) -> None:
    """Print a single reference case's deviation table."""
    result = calculate(case["input"])
    alt = result.alternatives[0]
    exp = case["expected"]
    cid = case["id"]

    print(f"\n{'=' * 72}")
    print(f"  {cid}")
    print(f"{'=' * 72}")

    # ── Top-level metrics ───────────────────────────────────────────

    if "hovedstol" in exp:
        actual = alt.total_hovedstol
        expected = exp["hovedstol"]
        print(
            f"  Hovedstol:   {_fmt_money(actual)}  ref={_fmt_money(expected)}  diff={_pct_diff(actual, expected)}"
        )

    if "ydelse" in exp:
        actual = _monthly_equiv_ydelse(alt, case["component_ppys"])
        expected = exp["ydelse"]
        print(
            f"  Ydelse:      {_fmt_money(actual)}  ref={_fmt_money(expected)}  diff={_pct_diff(actual, expected)}"
        )

    if "aap" in exp:
        actual = _effective_aap(alt, case["aap_ppy"])
        expected = exp["aap"]
        print(
            f"  ÅOP:         {_fmt_pct(actual)}  ref={_fmt_pct(expected)}  diff={_pp_diff(actual, expected)}"
        )

    # ── Horizon (0% shock) ─────────────────────────────────────────

    h0 = exp.get("horizon_0pct")
    if h0:
        row = _horizon_row(result, 0, Decimal(0))
        print("  --- Horizon (0% shock) ---")
        if "rente" in h0:
            print(
                f"  Rente:       {_fmt_money(row.rente_total)}  ref={_fmt_money(h0['rente'])}  diff={_pct_diff(row.rente_total, h0['rente'])}"
            )
        if "afdrag" in h0:
            print(
                f"  Afdrag:      {_fmt_money(row.afdrag_total)}  ref={_fmt_money(h0['afdrag'])}  diff={_pct_diff(row.afdrag_total, h0['afdrag'])}"
            )
        if "ydelse" in h0:
            print(
                f"  Ydelse:      {_fmt_money(row.ydelse_total)}  ref={_fmt_money(h0['ydelse'])}  diff={_pct_diff(row.ydelse_total, h0['ydelse'])}"
            )
        if "restgaeld" in h0:
            print(
                f"  Restgæld:    {_fmt_money(row.restgaeld)}  ref={_fmt_money(h0['restgaeld'])}  diff={_pct_diff(row.restgaeld, h0['restgaeld'])}"
            )

    # ── Horizon gns_kurs (all shocks) ──────────────────────────────

    gns_refs = exp.get("horizon_gns_kurs")
    if gns_refs:
        for shock, expected_kurs in gns_refs.items():
            row = _horizon_row(result, 0, shock)
            actual_kurs = row.gns_kurs
            shock_label = f"{int(float(shock) * 100):+d}%"
            print(
                f"  Gns.kurs ({shock_label:>3}):   {float(actual_kurs):>8.2f}  ref={float(expected_kurs):>8.2f}  diff={_kurs_diff(actual_kurs, expected_kurs)}"
            )


def main() -> None:
    print("boligregner-os correctness report")
    print(f"Reference data: boligregner.dk (captured Oct 5–8, 2026)")
    print(f"Cases: {len(REFERENCE_CASES)}")
    print(f"See docs/2026-10-07-boligregner-reference-data.md for provenance.")

    for case in REFERENCE_CASES:
        report_case(case)

    print()


if __name__ == "__main__":
    main()
