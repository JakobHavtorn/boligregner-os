"""Pydantic schemas for the mortgage calculator.

These mirror the table structure on boligregner.dk/resultater/beregning:
  - CalculatorInput: the parameters a user supplies (provenu, loan-type, rates, dates)
  - CalculatorResult: the three-alternative comparison + horizon analysis
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

# ─── Loan-type taxonomy ──────────────────────────────────────────────


class LoanType(str, Enum):
    """Danish realkredit loan types (obligationslån vs rentetilpasningslån)."""

    FIXED = "fixed"  # fast rente obligationslån (e.g. 4% obligation)
    F3 = "f3"  # rentetilpasningslån, 3-årlig justering (F3)
    F5 = "f5"  # rentetilpasningslån, 5-årlig justering (F5)
    F1 = "f1"  # rentetilpasningslån, 1-årlig justering (F1)
    T = "t"  # T-lån: fast ydelse, variabel løbetid (fixed payment, variable duration)
    CITA = "cita"  # CITA-referencerente (short-period variable rate)
    CIBOR = "cibor"  # CIBOR-referencerente (being phased out, replaced by DESTR)
    DESTR = "destr"  # DESTR (compounded overnight rate, replacing CIBOR)


class LoanComponent(str, Enum):
    """A financing alternative is composed of a realkredit part + a bank part."""

    REALKREDIT = "realkredit"
    BANK = "bank"


class Ejendomstype(str, Enum):
    """Property type determining the realkredit LTV bracket (belåningsgrænse)."""

    PRIVATE = "private"  # Ejerbolig til helårsbrug: 80%
    LEISURE = "leisure"  # Fritidsbolig: 75%
    BUSINESS = "business"  # Erhverv: 70%


LTV_BRACKETS: dict[Ejendomstype, Decimal] = {
    Ejendomstype.PRIVATE: Decimal("0.80"),
    Ejendomstype.LEISURE: Decimal("0.75"),
    Ejendomstype.BUSINESS: Decimal("0.70"),
}

# ─── Inputs ──────────────────────────────────────────────────────────


class LoanSpec(BaseModel):
    """Specification for one loan component within a financing alternative.

    For a realkredit obligation: set coupon_rate (e.g. 0.04 for a 4% obligation)
    and price (the current obligation price, e.g. 94.52).
    For a flexlån (F1/F3/F5): set rate directly; price is 100.00 (par).

    hovedstol is derived from provenu by the engine — left as None here.
    """

    component: LoanComponent
    loan_type: LoanType
    rate: Decimal = Field(
        ...,
        description="Nominal annual interest rate as a fraction: 0.04 = 4%. "
        "For FIXED and F1/F3/F5/T loans this is the coupon/nominal rate. "
        "For CITA/CIBOR/DESTR this is auto-computed as reference_rate + margin "
        "and must not be set manually.",
    )
    price: Decimal = Field(
        default=Decimal(100),
        description="Current price/kurs of the obligation. "
        "100 for flexlån (par); e.g. 94.52 for a discounted 4% obligation.",
    )
    maturity_years: int = Field(..., ge=1, le=40, description="Loan term in years.")
    issue_costs_pct: Decimal = Field(
        default=Decimal(0),
        description="Udstedelsesomkostninger as a fraction of hovedstol, e.g. 0.0177. "
        "Mutually exclusive with issue_costs_nominal.",
    )
    issue_costs_nominal: Decimal | None = Field(
        default=None,
        description="Udstedelsesomkostninger as a fixed kr. amount. "
        "When set, overrides issue_costs_pct; the engine converts it to an "
        "effective percentage for hovedstol derivation.",
    )
    redemption_price: Decimal = Field(
        default=Decimal(100),
        description="Indfrielseskurs as a percent of hovedstol, used in horizon payoff.",
    )
    provenu_share: Decimal = Field(
        default=Decimal(1),
        description="Fraction of desired_provenu this component funds. "
        "Components in one alternative should sum to 1.0. Default 1.0 = sole component.",
    )
    bidragssats: Decimal = Field(
        default=Decimal(0),
        description="Annual administration fee (bidrag) as a fraction of hovedstol, "
        "e.g. 0.0055 = 0.55%/year. Compounded into the effective rate for "
        "ydelse and ÅOP.",
    )
    interest_only_years: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Years of interest-only payments at the start (afdragsfrihed). "
        "0 = standard annuity from day one.",
    )
    payments_per_year: int = Field(
        default=4,
        ge=1,
        le=12,
        description="Payment frequency per year. Danish realkredit uses 4 (quarterly); "
        "bank loans may use 12 (monthly). Default 4 matches boligregner.dk.",
    )
    fixed_ydelse: Decimal | None = Field(
        default=None,
        description="Fixed monthly ydelse for T-lån (overrides annuity calculation). "
        "The loan term adjusts to fit this payment.",
    )
    reference_rate: Decimal | None = Field(
        default=None,
        description="Underlying market benchmark rate as an annual fraction, e.g. "
        "CITA, CIBOR, or DESTR. Required for CITA/CIBOR/DESTR loans; "
        "forbidden otherwise. The bank's margin is added on top.",
    )
    margin: Decimal | None = Field(
        default=None,
        description="The bank's spread above the reference rate, as an annual fraction. "
        "Required for CITA/CIBOR/DESTR; forbidden otherwise. "
        "The borrower's effective rate = reference_rate + margin.",
    )
    par_cap: bool = Field(
        default=False,
        description="When True, cap the realkredit hovedstol at the component's "
        "provenu share (par). Used for flexlån with bank loans where "
        "boligregner.dk caps the realkredit at par and gives the bank "
        "the residual. Default False preserves existing behavior.",
    )
    coupon_rate: Decimal | None = Field(
        default=None,
        description="Coupon rate of an existing bond, as an annual fraction. "
        "When set, the annuity payment is computed at this rate (the bond's "
        "contractual coupon) while `rate` remains the current market yield "
        "used for pricing and rate-shock scenarios. When None (default), "
        "`rate` is used for both. Used for deep-discount bonds where the "
        "coupon differs from the current market yield.",
    )

    @field_validator("rate")
    @classmethod
    def rate_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("rate must be >= 0")
        return v

    @field_validator("price")
    @classmethod
    def price_range(cls, v: Decimal) -> Decimal:
        if not (0 < v <= Decimal(200)):
            raise ValueError("price must be in (0, 200]")
        return v

    @field_validator("coupon_rate")
    @classmethod
    def coupon_rate_non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("coupon_rate must be >= 0")
        return v

    @model_validator(mode="after")
    def validate_loan_constraints(self) -> LoanSpec:
        # Afdragsfrihed: interest_only_years must be < maturity_years
        if self.interest_only_years >= self.maturity_years:
            raise ValueError("interest_only_years must be less than maturity_years")
        # T-lån: requires fixed_ydelse; fixed_ydelse only for T-lån
        if self.loan_type == LoanType.T and self.fixed_ydelse is None:
            raise ValueError("T-lån requires fixed_ydelse")
        if self.loan_type != LoanType.T and self.fixed_ydelse is not None:
            raise ValueError("fixed_ydelse is only for T-lån")
        # CITA/CIBOR/DESTR: reference_rate and margin required; rate is derived
        ref_types = (LoanType.CITA, LoanType.CIBOR, LoanType.DESTR)
        if self.loan_type in ref_types:
            if self.reference_rate is None:
                raise ValueError(
                    f"{self.loan_type.value.upper()} requires reference_rate"
                )
            if self.margin is None:
                raise ValueError(f"{self.loan_type.value.upper()} requires margin")
            # Auto-compute rate from reference + margin to prevent divergence
            self.rate = self.reference_rate + self.margin
        else:
            if self.reference_rate is not None:
                raise ValueError("reference_rate is only for CITA/CIBOR/DESTR")
            if self.margin is not None:
                raise ValueError("margin is only for CITA/CIBOR/DESTR")
        return self


class FinancingAlternative(BaseModel):
    """One financing proposal: typically a realkredit loan + a bank loan."""

    label: str = Field(
        ..., description="Display name, e.g. '30 år F3 januar, 30 år Banklån'."
    )
    components: list[LoanSpec] = Field(
        ..., min_length=1, description="The loan parts that make up this alternative."
    )

    @field_validator("components")
    @classmethod
    def at_least_one_realkredit(cls, comps: list[LoanSpec]) -> list[LoanSpec]:
        if not any(c.component == LoanComponent.REALKREDIT for c in comps):
            raise ValueError(
                "An alternative must include at least one realkredit component"
            )
        return comps


class CalculatorInput(BaseModel):
    """Top-level input to calculate()."""

    desired_provenu: Decimal = Field(
        ...,
        ge=Decimal(0),
        description="Ønsket provenu — net cash the borrower wants to receive.",
    )
    alternatives: list[FinancingAlternative] = Field(
        ..., min_length=1, description="Financing alternatives to compare."
    )
    start_date: date = Field(
        ..., description="Dato for lånets oprettelse / konvertering."
    )
    horizon_years: int = Field(
        default=5, ge=1, le=30, description="Horisontperiode in years."
    )
    tax_rate: Decimal = Field(
        default=Decimal("0.336"),
        description="Marginal skat (efter skat): 0.336 = 33,6% tax rate → interest deduction reduces cost by this fraction.",
    )
    rate_shocks: list[Decimal] = Field(
        default_factory=lambda: [Decimal("-0.02"), Decimal(0), Decimal("+0.02")],
        description="Renteændring scenarios for horizon analysis: -0.02, 0, +0.02 → -2%, 0%, +2%.",
    )
    ejendomsvaerdi: Decimal | None = Field(
        default=None,
        ge=Decimal(0),
        description="Ejendomsværdi — property value. When set together with "
        "ejendomstype, the realkredit/banklån split is auto-computed from "
        "the LTV bracket instead of using manual provenu_share values.",
    )
    ejendomstype: Ejendomstype | None = Field(
        default=None,
        description="Ejendomstype determining the realkredit belåningsgrænse "
        "(LTV bracket). Required when ejendomsvaerdi is set.",
    )

    @model_validator(mode="after")
    def validate_ejendom_fields(self) -> CalculatorInput:
        if self.ejendomsvaerdi is not None and self.ejendomstype is None:
            raise ValueError("ejendomstype is required when ejendomsvaerdi is set")
        if self.ejendomstype is not None and self.ejendomsvaerdi is None:
            raise ValueError("ejendomsvaerdi is required when ejendomstype is set")
        return self


# ─── Outputs ─────────────────────────────────────────────────────────


class LoanComponentResult(BaseModel):
    """Per-component computed numbers."""

    component: LoanComponent
    loan_type: LoanType
    hovedstol: Decimal
    obligationshovedstol: Decimal | None = (
        None  # Bond face value (only for kontantlån where it differs from hovedstol)
    )
    gns_kurs: Decimal  # gennemsnitlig kurs
    kursvaerdi: Decimal  # kursværdi
    udstedelsesomkostning: Decimal
    kontant: Decimal  # actual cash received
    ydelse_before_tax: Decimal  # monthly payment incl. afdrag+rente+bidrag
    ydelse_after_tax: Decimal
    aap_before_tax: Decimal  # ÅOP før skat (annual cost in percent)
    interest_only_years: int = 0  # Years of afdragsfrihed (0 = standard annuity)
    actual_maturity_years: Decimal | None = (
        None  # Actual term for T-lån (variable duration)
    )
    par_capped: bool = (
        False  # True when par_cap fired (hovedstol was capped at provenu)
    )


class AlternativeSummary(BaseModel):
    """One row in the top comparison table."""

    label: str
    total_hovedstol: Decimal
    gns_kurs: Decimal  # weighted average kurs
    total_kursvaerdi: Decimal
    total_udstedelsesomkostning: Decimal
    total_kontant: Decimal
    ydelse_before_tax: Decimal  # aggregated monthly payment at start
    ydelse_after_tax: Decimal
    aap_before_tax: Decimal  # blended ÅOP
    components: list[LoanComponentResult]


class ScenarioRow(BaseModel):
    """One row in the horizon (periodeomkostning) table for a given rate shock."""

    rate_shock: Decimal  # the shock applied, e.g. -0.02
    ydelse_start: Decimal  # monthly payment after tax at period start
    ydelse_slut: Decimal  # monthly payment after tax at horizon (after shock)
    rente_total: Decimal  # total interest+bidrag over period, after tax
    afdrag_total: Decimal  # total principal paid over period
    ydelse_total: Decimal  # rente_total + afdrag_total
    restgaeld: Decimal  # remaining debt at horizon
    gns_kurs: Decimal  # weighted avg redemption price at horizon
    indfrielse: Decimal  # total payoff amount incl. costs at horizon
    periodeomkostning: Decimal  # ydelse_total + indfrielse − provenu (after tax)


class HorizonAnalysis(BaseModel):
    """5-year horizon analysis for one alternative."""

    alternative_label: str
    scenarios: list[ScenarioRow]


class CalculatorResult(BaseModel):
    """Full result returned by calculate()."""

    desired_provenu: Decimal
    ejendomsvaerdi: Decimal | None = None
    start_date: date
    horizon_date: date
    tax_rate: Decimal
    alternatives: list[AlternativeSummary]
    horizon_analyses: list[HorizonAnalysis]


class AmortizationYear(BaseModel):
    """One year of an amortization schedule (afdragstabel)."""

    year: int  # 1-based year number
    ydelse: Decimal  # total payment this year (12 months)
    rente: Decimal  # interest portion (before tax)
    afdrag: Decimal  # principal portion
    restgaeld: Decimal  # remaining debt at end of year
    rente_after_tax: Decimal  # interest after tax deduction


class AmortizationSchedule(BaseModel):
    """Full year-by-year amortization schedule for one alternative."""

    alternative_label: str
    years: list[AmortizationYear]
