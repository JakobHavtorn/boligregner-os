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
    "calculate",
    "amortization_schedule",
    "CalculatorInput",
    "CalculatorResult",
    "FinancingAlternative",
    "LoanSpec",
    "LoanType",
    "LoanComponent",
]
