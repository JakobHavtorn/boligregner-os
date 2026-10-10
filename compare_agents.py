"""Compare engine output between base and agent branch for specific reference cases.

Runs key reference cases and prints the metrics that each agent was supposed to improve,
so we can see the before/after delta.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "src")

from boligregner import calculate
from boligregner.models import (
    CalculatorInput,
    FinancingAlternative,
    LoanComponent,
    LoanSpec,
    LoanType,
)

# ── T-lån 21 (Agent 1 target: ÅOP improvement) ───────────────────
tlaan_21 = CalculatorInput(
    desired_provenu=Decimal(4371947),
    start_date=date(2023, 4, 24),
    horizon_years=5,
    tax_rate=Decimal("0.336"),
    alternatives=[
        FinancingAlternative(
            label="F1 T 21",
            components=[
                LoanSpec(
                    component=LoanComponent.REALKREDIT,
                    loan_type=LoanType.T,
                    rate=Decimal("0.0372"),
                    price=Decimal("99.05"),
                    maturity_years=30,
                    issue_costs_nominal=Decimal(81053),
                    bidragssats=Decimal("0.0048"),
                    fixed_ydelse=Decimal("77585.40"),
                    provenu_share=Decimal(1),
                ),
            ],
        ),
    ],
)

# ── F1 20 (Agent 1 target: hovedstol/kursvaerdi) ──────────────────
f1_20 = CalculatorInput(
    desired_provenu=Decimal(2658833),
    start_date=date(2023, 4, 24),
    horizon_years=5,
    tax_rate=Decimal("0.336"),
    alternatives=[
        FinancingAlternative(
            label="F1 20",
            components=[
                LoanSpec(
                    component=LoanComponent.REALKREDIT,
                    loan_type=LoanType.FIXED,
                    rate=Decimal("0.0196"),
                    price=Decimal("100"),
                    maturity_years=20,
                    issue_costs_pct=Decimal("0.0177"),
                    bidragssats=Decimal("0.005"),
                    provenu_share=Decimal(1),
                ),
            ],
        ),
    ],
)


def run_case(label, inp, expected_hovedstol=None, expected_aap=None):
    result = calculate(inp)
    alt = result.alternatives[0]
    print(f"\n  {label}:")
    aap = alt.aap_before_tax
    print(f"    AAP (ÅOP):  {aap}", end="")
    if expected_aap is not None:
        gap = abs(aap - expected_aap) / expected_aap * 100
        print(f"  (expected: {expected_aap}, gap: {gap:.3f}%)")
    else:
        print()
    hs = alt.total_hovedstol
    print(f"    Hovedstol:  {hs}", end="")
    if expected_hovedstol is not None:
        gap = abs(hs - expected_hovedstol)
        pct = gap / expected_hovedstol * 100
        print(f"  (expected: {expected_hovedstol}, gap: {gap} kr ({pct:.2f}%)")
    else:
        print()
    print(f"    Ydelse:     {alt.ydelse_before_tax}")
    print(f"    Kursværdi:  {alt.total_kursvaerdi}")
    for i, comp in enumerate(alt.components):
        print(
            f"    Component {i}: aap={comp.aap_before_tax}, hovedstol={comp.hovedstol}, kursvaerdi={comp.kursvaerdi}"
        )


if __name__ == "__main__":
    print("=== T-lån 21 (Agent 1 target: ÅOP ~0.4pp improvement) ===")
    run_case(
        "T-lån 21",
        tlaan_21,
        expected_hovedstol=Decimal(4453000),
        expected_aap=Decimal("0.0444"),
    )

    print("\n=== F1 20 (Agent 1 target: hovedstol/kursvaerdi) ===")
    run_case("F1 20", f1_20, expected_hovedstol=Decimal(2718000))
