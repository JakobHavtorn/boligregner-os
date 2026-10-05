"""MCP server wrapping the boligregner calculation engine as a callable tool.

Exposes two things to an MCP client:

* ``calculate_mortgage`` — a tool that builds a :class:`CalculatorInput` from a
  flat JSON dict (optionally starting from a named preset), runs
  :func:`boligregner.calculate`, and returns the full :class:`CalculatorResult`
  as JSON.
* ``boligregner://presets`` — a resource listing the available preset names.

The server is built with the ``mcp`` 2.x SDK (``MCPServer``, formerly
``FastMCP``).  Run it over stdio via :mod:`boligregner.mcp_cli`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from .engine import PRESETS, calculate
from .models import (
    CalculatorInput,
    FinancingAlternative,
    LoanComponent,
    LoanSpec,
    LoanType,
)

# The server instance.  Importable as ``from boligregner.mcp_server import mcp``.
mcp = MCPServer(
    name="boligregner-os",
    title="Boligregner OS",
    description=(
        "Danish realkredit mortgage calculator: compare financing alternatives "
        "with 5-year horizon (periodeomkostning) analysis."
    ),
)


# ─── Input schema (flat JSON → CalculatorInput) ──────────────────────


class _ComponentSpec(BaseModel):
    """One loan component as supplied over the wire."""

    component: LoanComponent = Field(..., description="'realkredit' or 'bank'.")
    loan_type: LoanType = Field(
        ...,
        description="'fixed' (obligationslån), 'f3' (3-årlig), 'f5' (5-årlig), "
        "'f1' (1-årlig).",
    )
    rate: float = Field(
        ..., description="Nominal annual rate as a fraction, e.g. 0.04 = 4%."
    )
    price: float = Field(
        default=100.0,
        description="Obligation price/kurs; 100 for flexlån. "
        "E.g. 94.52 for a discounted 4% obligation.",
    )
    maturity_years: int = Field(..., ge=1, le=40, description="Loan term in years.")
    issue_costs_pct: float = Field(
        default=0.0,
        description="Udstedelsesomkostninger as a fraction of hovedstol, e.g. 0.0177.",
    )
    bidragssats: float = Field(
        default=0.0,
        description="Annual admin fee as a fraction of hovedstol, "
        "e.g. 0.0055 = 0.55%/yr. Added to effective rate.",
    )
    provenu_share: float = Field(
        default=1.0,
        description="Fraction of desired_provenu this component funds. "
        "Components in one alternative should sum to 1.0.",
    )
    redemption_price: float = Field(
        default=100.0,
        description="Indfrielseskurs (percent of hovedstol) used in horizon payoff.",
    )


class _AlternativeSpec(BaseModel):
    """One financing alternative as supplied over the wire."""

    label: str = Field(..., description="Display name for the alternative.")
    components: list[_ComponentSpec] = Field(
        ..., min_length=1, description="The loan parts making up this alternative."
    )


class MortgageInput(BaseModel):
    """Flat JSON schema for the ``calculate_mortgage`` tool arguments.

    Mirrors :class:`boligregner.models.CalculatorInput` but with plain JSON
    types (numbers instead of :class:`~decimal.Decimal`).  When ``preset`` is
    provided the named preset is loaded first and any fields present here
    override the preset's values.
    """

    desired_provenu: float | None = Field(
        default=None,
        description="Ønsket provenu — net cash the borrower wants to receive (kr).",
    )
    alternatives: list[_AlternativeSpec] | None = Field(
        default=None,
        description="Financing alternatives to compare. Each has a label and "
        "a components array.",
    )
    start_date: str | None = Field(
        default=None,
        description="Loan start date as YYYY-MM-DD.",
    )
    horizon_years: int | None = Field(
        default=None, ge=1, le=30, description="Horizon period in years."
    )
    tax_rate: float | None = Field(
        default=None,
        description="Marginal tax rate, e.g. 0.336 = 33.6%.",
    )
    rate_shocks: list[float] | None = Field(
        default=None,
        description="Rate-shock scenarios for horizon analysis, e.g. [-0.02, 0, 0.02].",
    )
    preset: str | None = Field(
        default=None,
        description="Name of a built-in preset to load before applying overrides. "
        "Use 'list_presets' to see available names.",
    )


# ─── Conversion helpers ──────────────────────────────────────────────


def _to_decimal(value: Any) -> Decimal:
    """Coerce a JSON number/string to :class:`~decimal.Decimal`."""
    return Decimal(str(value))


def _build_component(spec: _ComponentSpec) -> LoanSpec:
    return LoanSpec(
        component=spec.component,
        loan_type=spec.loan_type,
        rate=_to_decimal(spec.rate),
        price=_to_decimal(spec.price),
        maturity_years=spec.maturity_years,
        issue_costs_pct=_to_decimal(spec.issue_costs_pct),
        bidragssats=_to_decimal(spec.bidragssats),
        provenu_share=_to_decimal(spec.provenu_share),
        redemption_price=_to_decimal(spec.redemption_price),
    )


def _build_alternative(spec: _AlternativeSpec) -> FinancingAlternative:
    return FinancingAlternative(
        label=spec.label,
        components=[_build_component(c) for c in spec.components],
    )


def _build_input(args: MortgageInput) -> CalculatorInput:
    """Construct a validated :class:`CalculatorInput` from tool arguments.

    When ``preset`` is set, start from that preset and overlay every field the
    caller explicitly provided.  When no preset is given, ``desired_provenu``,
    ``alternatives``, and ``start_date`` are required.

    Raises :class:`ToolError` for any malformed value so the MCP client
    receives a readable ``is_error`` result instead of a generic crash.
    """
    try:
        return _build_input_inner(args)
    except ToolError:
        raise
    except (ValueError, InvalidOperation) as exc:
        raise ToolError(str(exc)) from exc


def _build_input_inner(args: MortgageInput) -> CalculatorInput:
    base: CalculatorInput | None = None
    if args.preset is not None:
        if args.preset not in PRESETS:
            raise ToolError(
                f"Unknown preset {args.preset!r}. "
                f"Available: {', '.join(sorted(PRESETS))}."
            )
        base = PRESETS[args.preset].model_copy(deep=True)

    # Required fields: either inherited from the preset or mandatory here.
    if args.desired_provenu is not None:
        provenu = _to_decimal(args.desired_provenu)
    elif base is not None:
        provenu = base.desired_provenu
    else:
        raise ToolError("desired_provenu is required when no preset is given.")

    if args.alternatives is not None:
        alternatives = [_build_alternative(a) for a in args.alternatives]
    elif base is not None:
        alternatives = base.alternatives
    else:
        raise ToolError("alternatives is required when no preset is given.")

    if args.start_date is not None:
        start = date.fromisoformat(args.start_date)
    elif base is not None:
        start = base.start_date
    else:
        raise ToolError("start_date is required when no preset is given.")

    horizon_years = (
        args.horizon_years
        if args.horizon_years is not None
        else (base.horizon_years if base is not None else 5)
    )
    tax_rate = (
        _to_decimal(args.tax_rate)
        if args.tax_rate is not None
        else (base.tax_rate if base is not None else Decimal("0.336"))
    )
    if args.rate_shocks is not None:
        rate_shocks = [_to_decimal(s) for s in args.rate_shocks]
    elif base is not None:
        rate_shocks = base.rate_shocks
    else:
        rate_shocks = [Decimal("-0.02"), Decimal("0"), Decimal("0.02")]

    return CalculatorInput(
        desired_provenu=provenu,
        alternatives=alternatives,
        start_date=start,
        horizon_years=horizon_years,
        tax_rate=tax_rate,
        rate_shocks=rate_shocks,
    )


# ─── Tools ───────────────────────────────────────────────────────────


@mcp.tool()
def calculate_mortgage(
    desired_provenu: float | None = None,
    alternatives: list[dict[str, Any]] | None = None,
    start_date: str | None = None,
    horizon_years: int | None = None,
    tax_rate: float | None = None,
    rate_shocks: list[float] | None = None,
    preset: str | None = None,
) -> dict[str, Any]:
    """Calculate Danish mortgage financing alternatives with 5-year horizon
    analysis. Returns comparison table and periodeomkostninger for -2%/0%/+2%
    rate scenarios.

    Pass ``preset`` to start from a built-in configuration (call ``list_presets``
    for the names); any other argument you supply overrides the preset.  When no
    preset is given, ``desired_provenu``, ``alternatives``, and ``start_date``
    are required.

    Parameters
    ----------
    desired_provenu:
        Net cash the borrower wants (kr), e.g. 2500000.
    alternatives:
        List of financing alternatives.  Each is ``{"label": str,
        "components": [{...}]}``; a component has ``component`` ("realkredit"
        or "bank"), ``loan_type`` ("fixed"/"f3"/"f5"/"f1"), ``rate``,
        ``price``, ``maturity_years``, ``issue_costs_pct``, ``bidragssats``,
        ``provenu_share``, ``redemption_price``.
    start_date:
        Loan start date as ``YYYY-MM-DD``.
    horizon_years:
        Horizon period for the analysis (default 5).
    tax_rate:
        Marginal tax rate (default 0.336).
    rate_shocks:
        Rate-shock scenarios (default [-0.02, 0, 0.02]).
    preset:
        Built-in preset name to load first.

    Returns
    -------
    dict
        The full calculation result: ``desired_provenu``, ``start_date``,
        ``horizon_date``, ``tax_rate``, ``alternatives`` (one summary per
        alternative), and ``horizon_analyses`` (one per alternative, each with
        a scenario row per rate shock).
    """
    # Validate the flat arguments through the Pydantic input model, which also
    # normalises aliases and applies range checks before we touch the engine.
    raw: dict[str, Any] = {}
    if desired_provenu is not None:
        raw["desired_provenu"] = desired_provenu
    if alternatives is not None:
        raw["alternatives"] = alternatives
    if start_date is not None:
        raw["start_date"] = start_date
    if horizon_years is not None:
        raw["horizon_years"] = horizon_years
    if tax_rate is not None:
        raw["tax_rate"] = tax_rate
    if rate_shocks is not None:
        raw["rate_shocks"] = rate_shocks
    if preset is not None:
        raw["preset"] = preset

    try:
        parsed = MortgageInput.model_validate(raw)
    except ValidationError as exc:
        # Surface a readable, single-message error to the agent.
        raise ToolError(str(exc)) from exc

    calc_input = _build_input(parsed)
    result = calculate(calc_input)
    return result.model_dump(mode="json")


@mcp.tool()
def list_presets() -> dict[str, Any]:
    """List the built-in mortgage calculation presets.

    Returns
    -------
    dict
        ``{"presets": [{"name": str, "description": str}, ...]}`` describing each
        ready-made configuration available for the ``preset`` argument of
        ``calculate_mortgage``.
    """
    descriptions: dict[str, str] = {
        "default": (
            "Three 30-year alternatives: F3 flexlån, F5 flexlån, and a 4% "
            "fixed obligation, each paired with a 30-year banklån. "
            "Provenu 2,500,000 kr, 5-year horizon, tax rate 33.6%."
        ),
    }
    presets = []
    for name in sorted(PRESETS):
        preset = PRESETS[name]
        alts = ", ".join(a.label for a in preset.alternatives)
        presets.append(
            {
                "name": name,
                "description": descriptions.get(
                    name,
                    f"{len(preset.alternatives)} alternative(s): {alts}.",
                ),
                "desired_provenu": str(preset.desired_provenu),
                "start_date": preset.start_date.isoformat(),
                "horizon_years": preset.horizon_years,
                "alternatives": [a.label for a in preset.alternatives],
            }
        )
    return {"presets": presets}


# ─── Resource ─────────────────────────────────────────────────────────


@mcp.resource("boligregner://presets")
def presets_resource() -> str:
    """List available preset names as a plain-text resource."""
    return ", ".join(sorted(PRESETS))
