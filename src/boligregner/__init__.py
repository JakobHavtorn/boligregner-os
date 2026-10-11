"""Boligregner OS — open-source Danish realkredit mortgage calculator."""

from .engine import PRESETS, amortization_schedule, calculate
from .models import (
    CalculatorInput,
    CalculatorResult,
    FinancingAlternative,
    LoanComponent,
    LoanSpec,
    LoanType,
)

__all__ = [
    "CalculatorInput",
    "CalculatorResult",
    "FinancingAlternative",
    "LoanComponent",
    "LoanSpec",
    "LoanType",
    "amortization_schedule",
    "calculate",
]
