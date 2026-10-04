"""Boligregner OS — open-source Danish realkredit mortgage calculator."""

from .models import (
    CalculatorInput,
    CalculatorResult,
    FinancingAlternative,
    LoanSpec,
    LoanType,
    LoanComponent,
)
from .engine import calculate, amortization_schedule, PRESETS

__all__ = [
    "calculate",
    "amortization_schedule",
    "CalculatorInput",
    "CalculatorResult",
    "FinancingAlternative",
    "LoanSpec",
    "LoanType",
    "LoanComponent",
]
