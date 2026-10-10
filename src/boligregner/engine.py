"""The calculation engine — the deep module.

Interface: calculate(CalculatorInput) -> CalculatorResult
  One function. Two types. All Danish realkredit math hidden behind it.

Implementation:
  - hovedstol derivation from desired provenu (inverts price + costs)
  - annuity amortization (level-payment mortgage math)
  - ÅOP via IRR on the net cash-flow stream (Newton's method)
  - 5-year horizon: amortize N months, shock the rate, amortize remainder
  - bond payoff at horizon (redemption_price × restgæld / 100)
  - periodeomkostning = ydelse_total + indfrielse − provenu (after tax)
  - interest-tax deduction (rentefradrag) at the marginal tax rate

All monetary values are Decimal for reproducibility.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, getcontext

from .models import (
    LTV_BRACKETS,
    AlternativeSummary,
    CalculatorInput,
    CalculatorResult,
    HorizonAnalysis,
    LoanComponent,
    LoanComponentResult,
    LoanSpec,
    LoanType,
    ScenarioRow,
)

getcontext().prec = 28

_TWO = Decimal(2)
_TWELVE = Decimal(12)
_HUNDRED = Decimal(100)
_ZERO = Decimal(0)
_ONE = Decimal(1)


# ─── Presets: ready-made loan alternatives matching boligregner.dk ─────


def _make_alt(
    label: str,
    realkredit_type: LoanType,
    realkredit_rate: Decimal,
    realkredit_price: Decimal,
    bank_rate: Decimal,
    bank_share: Decimal,
    issue_pct: Decimal,
    realkredit_bidrag: Decimal = Decimal("0.006"),
    bank_bidrag: Decimal = Decimal(0),
    interest_only_years: int = 0,
    fixed_ydelse: Decimal | None = None,
    reference_rate: Decimal | None = None,
    margin: Decimal | None = None,
    par_cap: bool = False,
) -> FinancingAlternative:  # type: ignore[name-defined]
    """Build a two-component alternative (realkredit + bank)."""
    from .models import FinancingAlternative

    realkredit_kwargs: dict = dict(
        component=LoanComponent.REALKREDIT,
        loan_type=realkredit_type,
        rate=realkredit_rate,
        price=realkredit_price,
        maturity_years=30,
        issue_costs_pct=issue_pct,
        bidragssats=realkredit_bidrag,
        provenu_share=_ONE - bank_share,
        par_cap=par_cap,
    )
    if interest_only_years:
        realkredit_kwargs["interest_only_years"] = interest_only_years
    if fixed_ydelse is not None:
        realkredit_kwargs["fixed_ydelse"] = fixed_ydelse
    if reference_rate is not None:
        realkredit_kwargs["reference_rate"] = reference_rate
    if margin is not None:
        realkredit_kwargs["margin"] = margin

    return FinancingAlternative(
        label=label,
        components=[
            LoanSpec(**realkredit_kwargs),
            LoanSpec(
                component=LoanComponent.BANK,
                loan_type=LoanType.F1,  # banklån: 1-årlig variabel
                rate=bank_rate,
                price=Decimal(100),
                maturity_years=30,
                issue_costs_pct=Decimal(0),
                provenu_share=bank_share,
                payments_per_year=12,  # bank loans pay monthly
            ),
        ],
    )


def _make_realkredit_only_alt(
    label: str,
    realkredit_type: LoanType,
    realkredit_rate: Decimal,
    realkredit_price: Decimal,
    issue_pct: Decimal,
    realkredit_bidrag: Decimal = Decimal("0.006"),
    interest_only_years: int = 0,
    fixed_ydelse: Decimal | None = None,
    reference_rate: Decimal | None = None,
    margin: Decimal | None = None,
) -> FinancingAlternative:  # type: ignore[name-defined]
    """Build a single-component realkredit-only alternative (no bank loan)."""
    from .models import FinancingAlternative

    realkredit_kwargs: dict = dict(
        component=LoanComponent.REALKREDIT,
        loan_type=realkredit_type,
        rate=realkredit_rate,
        price=realkredit_price,
        maturity_years=30,
        issue_costs_pct=issue_pct,
        bidragssats=realkredit_bidrag,
        provenu_share=_ONE,
    )
    if interest_only_years:
        realkredit_kwargs["interest_only_years"] = interest_only_years
    if fixed_ydelse is not None:
        realkredit_kwargs["fixed_ydelse"] = fixed_ydelse
    if reference_rate is not None:
        realkredit_kwargs["reference_rate"] = reference_rate
    if margin is not None:
        realkredit_kwargs["margin"] = margin

    return FinancingAlternative(
        label=label,
        components=[LoanSpec(**realkredit_kwargs)],
    )


PRESETS: dict[str, CalculatorInput] = {
    "default": CalculatorInput(
        desired_provenu=Decimal(2500000),
        start_date=date(2026, 10, 2),
        horizon_years=5,
        tax_rate=Decimal("0.336"),
        alternatives=[
            # Alt 1: 30-yr DESTR
            _make_realkredit_only_alt(
                label="30 år DESTR",
                realkredit_type=LoanType.DESTR,
                realkredit_rate=Decimal("0.0340"),  # total = reference + margin
                realkredit_price=Decimal(100),
                issue_pct=Decimal("0.0177"),
                reference_rate=Decimal("0.0315"),
                margin=Decimal("0.0025"),
            ),
            # Alt 2: 30-yr F1 flexlån
            _make_realkredit_only_alt(
                label="30 år F1",
                realkredit_type=LoanType.F1,
                realkredit_rate=Decimal("0.036"),
                realkredit_price=Decimal(100),  # par for flexlån
                issue_pct=Decimal("0.0177"),
            ),
            # Alt 3: 30-yr 4% obligation
            _make_realkredit_only_alt(
                label="30 år 4% obligation",
                realkredit_type=LoanType.FIXED,
                realkredit_rate=Decimal("0.04"),  # 4% coupon
                realkredit_price=Decimal("94.52"),  # trading at discount
                issue_pct=Decimal("0.0171"),
            ),
        ],
    ),
}


# ─── Core amortization primitives ───────────────────────────────────


def _periodic_rate(annual_rate: Decimal, ppy: int) -> Decimal:
    """Convert nominal annual rate to periodic rate given payments per year."""
    return annual_rate / Decimal(ppy)


def _monthly_rate(annual_rate: Decimal) -> Decimal:
    """Convert nominal annual rate to monthly periodic rate."""
    return _periodic_rate(annual_rate, 12)


def _effective_rate(spec: LoanSpec) -> Decimal:
    """Effective annual rate for amortization/ÅOP: rate + bidragssats.

    For CITA/CIBOR/DESTR the rate field is auto-computed as reference_rate + margin
    by the model validator, so this is always consistent with _rate_path().
    """
    return spec.rate + spec.bidragssats


# ─── Rate-path abstraction: universal per-month rate array ──────────


def _daily_to_monthly(annual_rate: Decimal) -> Decimal:
    """Convert a daily-compounded annual rate to a monthly equivalent.

    Uses the approximation: monthly = (1 + annual/360)^30 - 1.
    All arithmetic in Decimal for precision.
    """
    return (_ONE + annual_rate / Decimal(360)) ** 30 - _ONE


def _rate_path(
    spec: LoanSpec, shock: Decimal, n_months: int, ppy: int = 12
) -> list[Decimal]:
    """Return a per-month effective annual rate array for n_months.

    - FIXED: constant (shock doesn't affect amortization rate, only bond price)
    - F1/F3/F5/T: constant with shock applied to the effective rate
    - CITA/CIBOR/DESTR: constant with shock applied to reference_rate only
      (not margin). Uses a constant-rate-per-scenario model (same as flexlån).
    - DESTR: the reference rate is daily-compounded, converted to a monthly
      equivalent via _daily_to_monthly().

    When bidrag_model == "split", bidragssats is excluded from the returned rates
    (it's charged separately as a fixed amount on the original hovedstol).
    When bidrag_model == "compounded" (default), the rates include bidragssats.
    Negative rates are valid (Danish reference rates were negative in the 2010s).
    """
    # In split mode, bidrag is excluded from rates (charged separately)
    bidrag = spec.bidragssats if spec.bidrag_model == "compounded" else _ZERO
    ref_types = (LoanType.CITA, LoanType.CIBOR, LoanType.DESTR)

    if spec.loan_type not in ref_types:
        # FIXED: shock doesn't affect amortization rate
        # F1/F3/F5/T: shock applies to the full effective rate
        if spec.loan_type == LoanType.FIXED:
            rate = spec.rate + bidrag
        else:
            rate = spec.rate + bidrag + shock
        return [rate] * n_months

    # CITA/CIBOR/DESTR: rate = (reference + shock) + margin + bidrag
    ref = spec.reference_rate  # type: ignore[union-attr]
    margin = spec.margin  # type: ignore[union-attr]
    shocked_ref = ref + shock
    if spec.loan_type == LoanType.DESTR:
        # DESTR: convert daily-compounded reference to a monthly equivalent,
        # then annualize so the engine can divide by 12 to recover the monthly
        # rate.  monthly_equiv = (1 + ref/360)^30 - 1; annual = monthly_equiv * 12.
        # Dividing by 12 recovers monthly_equiv, preserving the compounding.
        monthly_equiv = _daily_to_monthly(shocked_ref)
        rate = monthly_equiv * _TWELVE + margin + bidrag
    else:
        # CITA/CIBOR: simple annual rate
        rate = shocked_ref + margin + bidrag
    return [rate] * n_months


def _rate_path_compounded(
    spec: LoanSpec, n_months: int, ppy: int = 12
) -> list[Decimal]:
    """Return a rate path always including bidragssats (compounded model).

    Used for ÅOP computation regardless of bidrag_model, since ÅOP must
    reflect the full effective cost (rate + bidrag) to match boligregner.dk.
    """
    eff = _effective_rate(spec)
    ref_types = (LoanType.CITA, LoanType.CIBOR, LoanType.DESTR)
    if spec.loan_type not in ref_types:
        return [eff] * n_months
    # CITA/CIBOR/DESTR: rate = reference + margin + bidrag
    ref = spec.reference_rate  # type: ignore[union-attr]
    margin = spec.margin  # type: ignore[union-attr]
    if spec.loan_type == LoanType.DESTR:
        monthly_equiv = _daily_to_monthly(ref)
        rate = monthly_equiv * _TWELVE + margin + spec.bidragssats
    else:
        rate = ref + margin + spec.bidragssats
    return [rate] * n_months


def _annuity_payment(
    hovedstol: Decimal, monthly_rate: Decimal, n_months: int
) -> Decimal:
    """Level monthly payment (ydelse) for an annuity mortgage.

    ydelse = hovedstol * r * (1+r)^n / ((1+r)^n − 1)
    Falls back to straight-line (hovedstol/n) when rate is zero.
    """
    n = Decimal(n_months)
    if monthly_rate == _ZERO:
        return hovedstol / n
    pow_n = (Decimal(1) + monthly_rate) ** n
    return hovedstol * monthly_rate * pow_n / (pow_n - Decimal(1))


def _solve_for_n(hovedstol: Decimal, monthly_rate: Decimal, payment: Decimal) -> int:
    """Solve for the number of months to amortize hovedstol at monthly_rate with fixed payment.

    From the annuity formula: payment = hovedstol * r * (1+r)^n / ((1+r)^n - 1)
    Solve for n: n = -ln(1 - hovedstol*r/payment) / ln(1+r)

    If payment <= hovedstol * r (payment doesn't cover interest), return -1 (never pays off).
    Uses Decimal.ln() for precision — no float contamination.
    """
    if payment <= _ZERO:
        return -1
    if monthly_rate == _ZERO:
        n = hovedstol / payment
        return int(n.to_integral_value(rounding=ROUND_CEILING))
    interest = hovedstol * monthly_rate
    if payment <= interest:
        return -1
    ratio = _ONE - interest / payment
    if ratio <= _ZERO:
        return -1
    # Decimal.ln() preserves precision (getcontext().prec = 28)
    n_dec = -ratio.ln() / (_ONE + monthly_rate).ln()
    n_ceil = int(n_dec.to_integral_value(rounding=ROUND_CEILING))
    # Verify: if balance after n_ceil-1 months is already ~0, use n_ceil-1
    if n_ceil > 1:
        balance = hovedstol
        r = monthly_rate
        for _ in range(n_ceil - 1):
            interest = balance * r
            principal = payment - interest
            if principal <= _ZERO:
                break
            balance -= principal
            if balance <= _ZERO:
                return n_ceil - 1
    return n_ceil


def _amortize(
    hovedstol: Decimal,
    rate_path: list[Decimal],
    n_months: int,
    interest_only_months: int = 0,
    payment: Decimal | None = None,
    ppy: int = 12,
    bidragssats: Decimal = _ZERO,
    bidrag_model: str = "compounded",
) -> Iterator[tuple[Decimal, Decimal, Decimal]]:
    """Yield (interest, principal, balance) for each period of an annuity loan.

    rate_path: per-period *annual* effective rates (including bidragssats
        when bidrag_model == "compounded"; nominal rate only when "split").
        The periodic rate for period i is rate_path[i] / ppy.
    interest_only_months: if > 0, the first N periods are interest-only
        (principal=0, balance unchanged).  The annuity payment is then
        computed over the remaining periods with the same balance.
    payment: if provided (T-lån mode), use this fixed periodic payment
        instead of computing it from n_months.  Amortize until balance
        reaches ~0 or n_months is exhausted.

    Since rate paths are constant per scenario, the payment is computed once
    at the start and never changes.

    interest  = balance * periodic_rate
    principal = payment − interest
    balance  -= principal

    When bidrag_model == "split", the rate_path excludes bidragssats (nominal
    rate only), and the annuity payment is computed at the nominal rate.  The
    bidrag charge (bidragssats * hovedstol / ppy) is NOT part of the yield —
    it's tracked separately by the caller.  The interest and principal here
    are purely the annuity amortization at the nominal rate.
    """
    balance = hovedstol

    # Determine the initial periodic payment (for non-T-lån)
    if payment is None:
        amort_months = n_months - interest_only_months
        r0 = _periodic_rate(rate_path[0], ppy) if rate_path else _ZERO
        current_payment = (
            _annuity_payment(hovedstol, r0, amort_months)
            if amort_months > 0
            else hovedstol * r0
        )
    else:
        current_payment = payment

    for month_idx in range(n_months):
        annual_r = rate_path[month_idx] if month_idx < len(rate_path) else rate_path[-1]
        r = _periodic_rate(annual_r, ppy)
        interest = balance * r

        if month_idx < interest_only_months:
            # Interest-only period
            yield interest, _ZERO, balance
            continue

        if balance <= _ZERO:
            yield _ZERO, _ZERO, _ZERO
            continue

        principal = current_payment - interest

        if principal < _ZERO:
            # Payment doesn't cover interest — balance grows (T-lån under shock)
            balance += interest - current_payment
            yield interest, _ZERO, balance
            continue

        balance -= principal
        if balance < _ZERO:
            # Final payment overpays slightly — clamp to zero
            principal = principal + balance
            balance = _ZERO
        yield interest, principal, balance


# ─── Hovedstol derivation: invert price + costs to hit desired provenu ─


def _is_kontantlaan(loan_type: LoanType) -> bool:
    """True for loan types that amortize on kontantlånshovedstol (at par).

    boligregner.dk's help text: "Hovedstol: For kontantlån angives
    kontantlånshovedstolen, for obligationslån obligationshovedstolen."

    Kontantlån (flexlån, reference-rate, T-lån) amortize on the mortgage
    amount (kursværdi = provenu + udst.omk), not the bond face value.
    Obligationslån (FIXED) amortize on the bond face value.
    """
    return loan_type in (
        LoanType.F1,
        LoanType.F3,
        LoanType.F5,
        LoanType.T,
        LoanType.CITA,
        LoanType.CIBOR,
        LoanType.DESTR,
    )


def _hovedstol_for_provenu(
    desired_provenu: Decimal,
    price: Decimal,
    issue_costs_pct: Decimal,
    issue_costs_nominal: Decimal | None = None,
    *,
    kontantlaan: bool = False,
) -> tuple[Decimal, Decimal]:
    """Given a desired net cash (provenu), derive (hovedstol, obligationshovedstol).

    For kontantlån (kontantlaan=True): hovedstol is derived at par (price=100).
        hovedstol = round_up_1000(provenu + udst.omk)
        obligationshovedstol = round_up_1000((provenu + udst.omk) / (kurs/100))

    For obligationslån (kontantlaan=False): hovedstol = obligationshovedstol.
        hovedstol = obligationshovedstol = round_up_1000((provenu + udst.omk) / (kurs/100))

    Percentage mode (issue_costs_pct set, nominal None):
        provenu = hovedstol * P/100 − hovedstol * c
                = hovedstol * (P/100 − c)
        → hovedstol = provenu / (P/100 − c)

    Nominal mode (issue_costs_nominal set):
        provenu = hovedstol * P/100 − nominal
        → hovedstol = (provenu + nominal) / (P/100)

    Round up to nearest thousand (boligregner rounds to whole thousands).
    """
    if kontantlaan:
        # Kontantlån: amortize at par (price=100), derive obligationshovedstol separately.
        if issue_costs_nominal is not None and issue_costs_nominal > _ZERO:
            raw_hoved = desired_provenu + issue_costs_nominal
        else:
            net_factor = _ONE - issue_costs_pct
            if net_factor <= _ZERO:
                raise ValueError(
                    f"Issue costs {issue_costs_pct} >= 1.0; "
                    "cannot derive kontantlån hovedstol"
                )
            raw_hoved = desired_provenu / net_factor
        hovedstol = _qceil(raw_hoved / Decimal(1000)) * Decimal(1000)

        # Obligationshovedstol: bond face value at the actual issue price.
        price_factor = price / _HUNDRED
        if price_factor > _ZERO:
            raw_oblig = raw_hoved / price_factor
            obligationshovedstol = _qceil(raw_oblig / Decimal(1000)) * Decimal(1000)
        else:
            obligationshovedstol = hovedstol  # at par when price=0 (shouldn't happen)
        return hovedstol, obligationshovedstol, raw_hoved

    # Obligationslån: hovedstol = obligationshovedstol (bond face value).
    # boligregner.dk applies a kurtage (bond issuance discount) of 0.1% (0.001)
    # to the effective price when issue costs are specified as a nominal amount.
    # provenu = hovedstol * (P/100 − kurtage) − nominal_omkostninger
    # In percentage mode, kurtage is not applied (matches existing behavior).
    if issue_costs_nominal is not None and issue_costs_nominal > _ZERO:
        kurtage = Decimal("0.001")
        price_factor = price / _HUNDRED - kurtage
        if price_factor <= _ZERO:
            raise ValueError(
                f"Price {price} must be positive to derive hovedstol with nominal issue costs"
            )
        raw = (desired_provenu + issue_costs_nominal) / price_factor
        hovedstol = (raw / Decimal(1000)).to_integral_value(
            rounding=ROUND_HALF_UP
        ) * Decimal(1000)
    else:
        net_factor = price / _HUNDRED - issue_costs_pct
        if net_factor <= _ZERO:
            raise ValueError(
                f"Price {price} minus issue costs {issue_costs_pct} yields non-positive provenu; "
                "cannot derive hovedstol"
            )
        raw = desired_provenu / net_factor
        hovedstol = _qceil(raw / Decimal(1000)) * Decimal(1000)
    return hovedstol, hovedstol, raw


def _qceil(x: Decimal) -> Decimal:
    """Quantized ceiling: smallest integer >= x, returned as Decimal."""
    from decimal import ROUND_CEILING

    return x.to_integral_value(rounding=ROUND_CEILING)


# ─── ÅOP (annual percentage rate of charge) via IRR ──────────────────


def _irr(
    cashflows: list[Decimal], guess: Decimal = Decimal("0.05"), tol: int = 30
) -> Decimal:
    """Internal rate of return via Newton's method on the NPV polynomial.

    cashflows[0] is the inflow (loan disbursement, negative cost to lender
    from borrower's perspective we treat as positive received); subsequent
    flows are outflows (monthly payments, positive).  IRR finds r such that
    NPV = Σ cf_i / (1+r)^i = 0.
    """
    r = guess
    for _ in range(tol):
        npv = _ZERO
        dnpv = _ZERO
        for i, cf in enumerate(cashflows):
            disc = (Decimal(1) + r) ** i
            npv += cf / disc
            if i > 0:
                dnpv -= Decimal(i) * cf / ((Decimal(1) + r) ** (i + 1))
        if dnpv == _ZERO:
            break
        step = npv / dnpv
        r -= step
        if abs(step) < Decimal("1e-12"):
            break
    return r


def _aap(
    hovedstol: Decimal,
    rate_path: list[Decimal],
    n_months: int,
    net_disbursement: Decimal,
    interest_only_months: int = 0,
    payment: Decimal | None = None,
    ppy: int = 12,
    bidragssats: Decimal = _ZERO,
    bidrag_model: str = "compounded",
) -> Decimal:
    """ÅOP (årlige omkostninger i procent) before tax.

    Build a periodic cash-flow stream: +net_disbursement at t=0,
    −payment each period.  IRR gives periodic rate; annualize × ppy.
    net_disbursement is the actual cash received (kursværdi − omkostninger),
    which for a discount obligation is less than hovedstol.

    rate_path: per-period annual effective rates.  When payments vary
        (resetting loans), the actual payment schedule is derived from
        the rate path via _amortize and used for the cashflow.
    interest_only_months: if > 0, the first N payments are interest-only.
    payment: if provided (T-lån), use this fixed payment for all periods.
    ppy: payments per year (12 monthly, 4 quarterly).
    bidragssats: annual bidrag fee as a fraction of hovedstol (split mode only).
    bidrag_model: "compounded" (default) or "split".  In split mode, the
        cashflows include the bidrag charge (bidragssats * hovedstol / ppy)
        so the IRR captures the full cost including bidrag.
    """
    r0 = _periodic_rate(rate_path[0], ppy) if rate_path else _ZERO
    cfs: list[Decimal] = [net_disbursement]
    # In split mode, bidrag is a separate fixed charge per period
    bidrag_charge = (
        bidragssats * hovedstol / Decimal(ppy)
        if bidrag_model == "split" and bidragssats > _ZERO
        else _ZERO
    )

    if payment is not None:
        # T-lån: fixed payment for the actual number of periods
        # In split mode, add bidrag charge to each cashflow
        cfs += [-(payment + bidrag_charge)] * n_months
    else:
        # Derive the actual payment schedule from the rate path
        # (handles interest-only, constant-rate-per-scenario, etc.)
        periodic_flows = list(
            _amortize(
                hovedstol,
                rate_path,
                n_months,
                interest_only_months=interest_only_months,
                ppy=ppy,
                bidragssats=bidragssats,
                bidrag_model=bidrag_model,
            )
        )
        for interest, principal, _ in periodic_flows:
            cfs.append(-(interest + principal + bidrag_charge))

    periodic_irr = _irr(cfs, guess=r0)
    return periodic_irr * Decimal(ppy)


# ─── Per-component computation ───────────────────────────────────────


def _compute_component(
    spec: LoanSpec,
    component_provenu: Decimal,
    tax_rate: Decimal,
) -> LoanComponentResult:
    """Compute all per-component numbers from a LoanSpec + the provenu slice."""
    kontantlaan = _is_kontantlaan(spec.loan_type)
    hovedstol, obligationshovedstol, raw_hovedstol = _hovedstol_for_provenu(
        component_provenu,
        spec.price,
        spec.issue_costs_pct,
        spec.issue_costs_nominal,
        kontantlaan=kontantlaan,
    )
    par_capped = False
    if spec.par_cap and hovedstol > component_provenu:
        # Par cap: hovedstol at par (= provenu), bond issued at 100
        hovedstol = component_provenu
        obligationshovedstol = component_provenu
        kursvaerdi = hovedstol  # at par, not price-discounted
        par_capped = True
    elif kontantlaan:
        # Kontantlån: kursværdi = hovedstol (at par)
        kursvaerdi = hovedstol
    else:
        # Obligationslån: kursværdi = unrounded hovedstol × kurs / 100
        # boligregner.dk computes kursværdi from the unrounded hovedstol,
        # not the rounded-to-1000 hovedstol, so kursværdi = provenu + udstedelse exactly.
        kursvaerdi = raw_hovedstol * spec.price / _HUNDRED
    if spec.issue_costs_nominal is not None and spec.issue_costs_nominal > _ZERO:
        udstedelse = spec.issue_costs_nominal
    else:
        udstedelse = hovedstol * spec.issue_costs_pct
    kontant = kursvaerdi - udstedelse

    ppy = spec.payments_per_year
    # In split mode, the rate path excludes bidrag (nominal rate only);
    # the annuity is computed at the nominal rate and bidrag is a separate charge.
    # In compounded mode, the rate path includes bidrag (existing behavior).
    if spec.bidrag_model == "split":
        annuity_rate = spec.rate
        bidrag_charge = spec.bidragssats * hovedstol / Decimal(ppy)
    else:
        annuity_rate = _effective_rate(spec)
        bidrag_charge = _ZERO
    r = _periodic_rate(annuity_rate, ppy)
    n = int(
        (spec.maturity_years * Decimal(ppy)).to_integral_value(rounding=ROUND_CEILING)
    )
    io_months = spec.interest_only_years * ppy

    # Build the rate path for amortization (split: nominal only; compounded: rate+bidrag)
    rate_path = _rate_path(spec, _ZERO, n, ppy)
    # ÅOP always uses the compounded rate path (rate + bidrag) to match boligregner.dk
    aap_rate_path = (
        rate_path
        if spec.bidrag_model == "compounded"
        else _rate_path_compounded(spec, n, ppy)
    )

    actual_maturity_years: Decimal | None = None

    if spec.loan_type == LoanType.T and spec.fixed_ydelse is not None:
        # T-lån: fixed payment, variable duration
        ydelse_bs = spec.fixed_ydelse + bidrag_charge
        actual_n = _solve_for_n(hovedstol, r, spec.fixed_ydelse)
        if actual_n < 0:
            actual_n = n  # fallback to nominal term
        actual_maturity_years = Decimal(actual_n) / Decimal(ppy)
        # After tax: first period's interest is tax-deductible
        first_interest = hovedstol * r
        ydelse_es = ydelse_bs - first_interest * tax_rate
        aap = _aap(
            hovedstol,
            aap_rate_path,
            actual_n,
            kontant,
            payment=spec.fixed_ydelse,
            ppy=ppy,
        )
        # T-lån payments are fixed at the nominal rate, so IRR captures only
        # the premium/discount on the issue price.  Add bidragssats
        # additively to get the full ÅOP (matches boligregner.dk).
        if spec.bidrag_model == "split" and spec.bidragssats > _ZERO:
            aap = aap + spec.bidragssats
    elif io_months > 0:
        # Afdragsfrihed: report the annuity payment over the remaining term
        amort_months = n - io_months
        annuity_payment = _annuity_payment(hovedstol, r, amort_months)
        ydelse_bs = annuity_payment + bidrag_charge
        first_interest = hovedstol * r
        ydelse_es = ydelse_bs - first_interest * tax_rate
        aap = _aap(
            hovedstol,
            aap_rate_path,
            n,
            kontant,
            interest_only_months=io_months,
            ppy=ppy,
        )
    else:
        # Standard annuity (incl. CITA/CIBOR/DESTR)
        annuity_payment = _annuity_payment(hovedstol, r, n)
        ydelse_bs = annuity_payment + bidrag_charge
        first_interest = hovedstol * r
        ydelse_es = ydelse_bs - first_interest * tax_rate
        aap = _aap(
            hovedstol,
            aap_rate_path,
            n,
            kontant,
            ppy=ppy,
        )

    return LoanComponentResult(
        component=spec.component,
        loan_type=spec.loan_type,
        hovedstol=hovedstol,
        obligationshovedstol=(
            obligationshovedstol
            if kontantlaan and obligationshovedstol != hovedstol
            else None
        ),
        gns_kurs=_HUNDRED if par_capped else spec.price,
        kursvaerdi=kursvaerdi,
        udstedelsesomkostning=udstedelse,
        kontant=kontant,
        ydelse_before_tax=ydelse_bs,
        ydelse_after_tax=ydelse_es,
        aap_before_tax=aap,
        interest_only_years=spec.interest_only_years,
        actual_maturity_years=actual_maturity_years,
        par_capped=par_capped,
    )


def _issue_yield(
    coupon_rate: Decimal,
    issue_price: Decimal,
    total_periods: int,
    ppy: int = 4,
) -> Decimal:
    """Compute the yield-to-maturity that prices a bond at issue_price.

    Used for pull-to-par: at 0% shock, discount bonds trade below par
    because they were issued below par and accrete toward par over time.
    """
    if issue_price >= _HUNDRED:
        return coupon_rate  # par or premium: yield = coupon

    lo = coupon_rate
    hi = coupon_rate * _TWO
    for _ in range(50):
        mid = (lo + hi) / _TWO
        price = _bond_price(coupon_rate, mid, _HUNDRED, total_periods, ppy)
        if price > issue_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / _TWO


def _bond_price(
    coupon_rate: Decimal,
    yield_rate: Decimal,
    remaining_balance: Decimal,
    remaining_periods: int,
    ppy: int = 12,
) -> Decimal:
    """Price a finite bond as PV of remaining cashflows.

    The bond pays coupon_rate on the declining balance each period,
    plus principal is repaid via the annuity schedule.

    Returns price as percent of remaining balance (e.g., 86.08 for 86.08%).
    """
    if yield_rate <= _ZERO:
        return _HUNDRED

    periodic_yield = yield_rate / Decimal(ppy)
    r = coupon_rate / Decimal(ppy)  # periodic coupon rate
    pv = _ZERO
    balance = remaining_balance
    n = Decimal(remaining_periods)
    if r == _ZERO:
        payment = remaining_balance / n
    else:
        pow_n = (_ONE + r) ** n
        payment = remaining_balance * r * pow_n / (pow_n - _ONE)

    for i in range(remaining_periods):
        interest = balance * r
        principal = payment - interest
        cashflow = interest + principal  # total payment
        pv += cashflow / (_ONE + periodic_yield) ** Decimal(i + 1)
        balance -= principal

    return pv / remaining_balance * _HUNDRED


def _bond_price_with_option(
    coupon_rate: Decimal,
    yield_rate: Decimal,
    remaining_balance: Decimal,
    remaining_periods: int,
    ppy: int = 12,
    prepayment_premium: Decimal = Decimal("0.005"),
) -> Decimal:
    """Finite-bond price capped at par + premium (prepayment option)."""
    option_free = _bond_price(
        coupon_rate, yield_rate, remaining_balance, remaining_periods, ppy
    )
    return min(option_free, _HUNDRED * (_ONE + prepayment_premium))


# ─── Horizon analysis: amortize, shock, compute periodeomkostning ─────


def _horizon_scenarios(
    components: list[tuple[LoanSpec, LoanComponentResult]],
    horizon_years: int,
    tax_rate: Decimal,
    rate_shocks: list[Decimal],
    total_provenu: Decimal,
) -> list[ScenarioRow]:
    """For each rate shock: amortize to horizon, apply shock, compute totals."""
    rows: list[ScenarioRow] = []

    for shock in rate_shocks:
        rente_total = _ZERO
        afdrag_total = _ZERO
        restgaeld_total = _ZERO
        indfrielse_total = _ZERO
        ydelse_slut_at = _ZERO
        weighted_price = _ZERO
        total_hoved = sum(r.hovedstol for _, r in components)
        total_balance = _ZERO

        # First pass: compute balances at horizon for all components
        component_data: list[
            tuple[
                LoanSpec,
                LoanComponentResult,
                Decimal,
                list[tuple[Decimal, Decimal, Decimal]],
                Decimal,
                int,
                Decimal,
                Decimal | None,
                list[Decimal],
                int,
                int,
            ]
        ] = []
        for spec, comp in components:
            ppy = spec.payments_per_year
            horizon_n = horizon_years * ppy
            n = int(
                (spec.maturity_years * Decimal(ppy)).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            io_months = spec.interest_only_years * ppy
            path = _rate_path(spec, shock, n, ppy)

            if spec.loan_type == LoanType.T and spec.fixed_ydelse is not None:
                fixed_payment = spec.fixed_ydelse
            else:
                fixed_payment = None

            monthly = list(
                _amortize(
                    comp.hovedstol,
                    path,
                    n,
                    interest_only_months=io_months,
                    payment=fixed_payment,
                    ppy=ppy,
                    bidragssats=spec.bidragssats,
                    bidrag_model=spec.bidrag_model,
                )
            )[:horizon_n]
            comp_interest = sum(m[0] for m in monthly)
            comp_principal = sum(m[1] for m in monthly)
            balance = monthly[-1][2] if monthly else comp.hovedstol
            total_balance += balance
            component_data.append(
                (
                    spec,
                    comp,
                    balance,
                    monthly,
                    comp_interest,
                    comp_principal,
                    path,
                    fixed_payment,
                    n,
                    horizon_n,
                    io_months,
                )
            )

        # Second pass: compute totals using balance-based weighting
        for (
            spec,
            comp,
            balance,
            monthly,
            comp_interest,
            comp_principal,
            path,
            fixed_payment,
            n,
            horizon_n,
            io_months,
        ) in component_data:
            ppy = spec.payments_per_year
            # In split mode, bidrag is a separate charge on original hovedstol.
            # The reference "rente" includes bidrag as part of the interest charge,
            # and ydelse includes it as part of the total payment.
            bidrag_charge = (
                spec.bidragssats * comp.hovedstol / Decimal(ppy)
                if spec.bidrag_model == "split"
                else _ZERO
            )
            bidrag_total = bidrag_charge * Decimal(horizon_n)
            # Bidrag tax treatment differs by loan type:
            # - FIXED (obligationslån): bidrag IS tax-deductible (SL § 6 stk. 1 e).
            #   boligregner.dk reports rente as (interest + bidrag) × (1 − tax).
            # - Flexlån (F1/F3/F5/T/CITA/CIBOR/DESTR): boligregner.dk reports rente
            #   as interest × (1 − tax) + bidrag (bidrag not tax-deductible in
            #   their reporting). This matches Session A reference data: F3
            #   rente gap improves from 11.2% to 0.6%, F5 from 9.4% to 0.9%.
            #   The 4% fixed case stays at 0.7% (unchanged, uses tax-deductible).
            if spec.bidrag_model == "split" and spec.loan_type != LoanType.FIXED:
                rente_total += comp_interest * (_ONE - tax_rate) + bidrag_total
            else:
                rente_total += (comp_interest + bidrag_total) * (_ONE - tax_rate)
            afdrag_total += comp_principal
            restgaeld_total += balance

            if spec.loan_type == LoanType.FIXED:
                # Pull-to-par: at 0% shock, use issue yield (from issue price)
                # so discount bonds price below par. At nonzero shocks, use
                # coupon rate as base (market yield = coupon + shock).
                base_yield = (
                    _issue_yield(spec.rate, spec.price, n, ppy)
                    if shock == _ZERO
                    else spec.rate
                )
                # Reduced-duration heuristic: callable bonds have lower effective
                # duration than straight bonds. Scale the shock by (1 - adjustment)
                # where adjustment is derived from prepayment_premium (higher premium
                # = more option value = more duration reduction). Only for nonzero
                # shocks; at 0% the issue_yield already accounts for the discount.
                if shock != _ZERO and spec.bond_price_model == "finite_option":
                    duration_adj = min(spec.prepayment_premium * Decimal(10), _ONE)
                    shocked_yield = base_yield + shock * (_ONE - duration_adj)
                else:
                    shocked_yield = base_yield + shock
                remaining_periods = n - horizon_n
                if spec.bond_price_model == "finite":
                    shocked_price = _bond_price(
                        spec.rate, shocked_yield, balance, remaining_periods, ppy
                    )
                elif spec.bond_price_model == "finite_option":
                    shocked_price = _bond_price_with_option(
                        spec.rate,
                        shocked_yield,
                        balance,
                        remaining_periods,
                        ppy,
                        spec.prepayment_premium,
                    )
                else:  # "simple" — existing perpetuity formula
                    shocked_price = (
                        _HUNDRED * spec.rate / shocked_yield
                        if shocked_yield != _ZERO
                        else _HUNDRED
                    )
                    # Clamp to reasonable bounds
                    shocked_price = min(
                        _HUNDRED * Decimal("1.1"), max(Decimal(50), shocked_price)
                    )
                payoff = balance * shocked_price / _HUNDRED
                # Weight gns_kurs by balance share for proper average across components.
                # For finite models, use total_balance (remaining balances) so
                # single-component gns_kurs equals the bond price directly.
                # For simple model, preserve existing balance/total_hoved behavior.
                weight_base = (
                    total_balance if spec.bond_price_model != "simple" else total_hoved
                )
                weighted_price += shocked_price * (
                    balance / weight_base if weight_base else _ZERO
                )
            else:
                payoff = balance * spec.redemption_price / _HUNDRED
                weighted_price += spec.redemption_price * (
                    balance / total_balance if total_balance else _ZERO
                )
            indfrielse_total += payoff

            # After-tax ydelse at horizon
            remaining = n - horizon_n
            # In split mode, bidrag charge is added to the before-tax ydelse
            bidrag_charge = (
                spec.bidragssats * comp.hovedstol / Decimal(ppy)
                if spec.bidrag_model == "split"
                else _ZERO
            )
            if remaining > 0:
                r_end = _periodic_rate(
                    path[horizon_n - 1] if horizon_n <= len(path) else path[-1], ppy
                )
                if fixed_payment is not None:
                    # T-lån: ydelse doesn't change with rate shock
                    ydelse_shocked = fixed_payment + bidrag_charge
                    first_interest_shocked = balance * r_end
                    ydelse_slut_at += ydelse_shocked - first_interest_shocked * tax_rate
                elif io_months > 0 and horizon_n < io_months:
                    # Still in interest-only period at horizon
                    ydelse_shocked = balance * r_end + bidrag_charge
                    first_interest_shocked = balance * r_end
                    ydelse_slut_at += ydelse_shocked - first_interest_shocked * tax_rate
                elif io_months > 0 and horizon_n >= io_months:
                    # Past interest-only: annuity on remaining balance over remaining amortization periods
                    remaining_amort = n - max(horizon_n, io_months)
                    if remaining_amort > 0:
                        ydelse_shocked = (
                            _annuity_payment(balance, r_end, remaining_amort)
                            + bidrag_charge
                        )
                        first_interest_shocked = balance * r_end
                        ydelse_slut_at += (
                            ydelse_shocked - first_interest_shocked * tax_rate
                        )
                else:
                    ydelse_shocked = (
                        _annuity_payment(balance, r_end, remaining) + bidrag_charge
                    )
                    first_interest_shocked = balance * r_end
                    ydelse_slut_at += ydelse_shocked - first_interest_shocked * tax_rate
        ydelse_total = rente_total + afdrag_total
        periodeomk = ydelse_total + indfrielse_total - total_provenu

        # After-tax ydelse at start (original rate, same for all shocks)
        ydelse_start_at = _monthly_payment_after_tax(
            components, tax_rate, shocked=False
        )

        rows.append(
            ScenarioRow(
                rate_shock=shock,
                ydelse_start=ydelse_start_at,
                ydelse_slut=ydelse_slut_at,
                rente_total=rente_total,
                afdrag_total=afdrag_total,
                ydelse_total=ydelse_total,
                restgaeld=restgaeld_total,
                gns_kurs=weighted_price if weighted_price > _ZERO else Decimal(100),
                indfrielse=indfrielse_total,
                periodeomkostning=periodeomk,
            )
        )

    return rows


def _monthly_payment_after_tax(
    components: list[tuple[LoanSpec, LoanComponentResult]],
    tax_rate: Decimal,
    shocked: bool,
) -> Decimal:
    """Sum of monthly payments after tax deduction on interest."""
    total = _ZERO
    for spec, comp in components:
        ppy = spec.payments_per_year
        # In split mode, use nominal rate for annuity and add bidrag charge
        if spec.bidrag_model == "split":
            annuity_rate = spec.rate
            bidrag_charge = spec.bidragssats * comp.hovedstol / Decimal(ppy)
        else:
            annuity_rate = _effective_rate(spec)
            bidrag_charge = _ZERO
        r = _periodic_rate(annuity_rate, ppy)
        n = int(
            (spec.maturity_years * Decimal(ppy)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        io_months = spec.interest_only_years * ppy
        if spec.loan_type == LoanType.T and spec.fixed_ydelse is not None:
            ydelse = spec.fixed_ydelse + bidrag_charge
        elif io_months > 0:
            # Report the post-interest-only annuity (long-term payment)
            ydelse = _annuity_payment(comp.hovedstol, r, n - io_months) + bidrag_charge
        else:
            ydelse = _annuity_payment(comp.hovedstol, r, n) + bidrag_charge
        interest = comp.hovedstol * r
        total += ydelse - interest * tax_rate
    return total


# ─── LTV-based realkredit/banklån split ─────────────────────────────


def _ltv_shares(
    input: CalculatorInput,
    components: list[LoanSpec],
) -> list[Decimal] | None:
    """Compute per-component provenu shares from the LTV bracket.

    When the user provides ejendomsværdi + ejendomstype, the total
    realkredit provenu is capped at ``ejendomsværdi × LTV%`` and the
    banklån covers the remainder.  Among multiple realkredit components
    the original ``provenu_share`` ratios are preserved (scaled to fit
    the cap).  Returns ``None`` when no LTV info is given, signalling
    the caller to use the manual ``provenu_share`` values on each spec.
    """
    if input.ejendomsvaerdi is None or input.ejendomstype is None:
        return None
    if input.desired_provenu <= _ZERO:
        return None

    ltv = LTV_BRACKETS[input.ejendomstype]
    max_realkredit_provenu = input.ejendomsvaerdi * ltv
    realkredit_cap = min(max_realkredit_provenu / input.desired_provenu, _ONE)

    # Original shares within realkredit and bank, used to distribute
    # the capped realkredit budget among multiple realkredit components.
    orig_realkredit = sum(
        s.provenu_share for s in components if s.component == LoanComponent.REALKREDIT
    )
    orig_bank = sum(
        s.provenu_share for s in components if s.component == LoanComponent.BANK
    )

    shares: list[Decimal] = []
    for spec in components:
        if spec.component == LoanComponent.REALKREDIT:
            if orig_realkredit > _ZERO:
                # Scale this component's realkredit share into the cap.
                shares.append(realkredit_cap * spec.provenu_share / orig_realkredit)
            else:
                shares.append(_ZERO)
        else:
            # Bank gets the remainder of provenu after the realkredit cap.
            if orig_bank > _ZERO:
                shares.append((_ONE - realkredit_cap) * spec.provenu_share / orig_bank)
            else:
                shares.append(_ZERO)
    return shares


# ─── The interface: calculate() ─────────────────────────────────────


def calculate(input: CalculatorInput) -> CalculatorResult:
    """Compute the full comparison + horizon analysis.

    This is the single entry point. Pass a CalculatorInput, get a
    CalculatorResult. All math (hovedstol derivation, annuity, ÅOP,
    horizon, periodeomkostning) is hidden inside.
    """
    horizon_years = input.horizon_years
    horizon_date = date(
        input.start_date.year + input.horizon_years,
        input.start_date.month,
        input.start_date.day,
    ) - timedelta(days=1)

    alt_summaries: list[AlternativeSummary] = []
    horizon_analyses: list[HorizonAnalysis] = []
    for alt in input.alternatives:
        comp_results: list[tuple[int, LoanSpec, LoanComponentResult]] = []
        ltv_shares = _ltv_shares(input, alt.components)
        # Two-pass: compute realkredit first, then bank as residual if par cap fired
        realkredit_kontant = _ZERO
        any_par_capped = False
        for i, spec in enumerate(alt.components):
            if spec.component != LoanComponent.REALKREDIT:
                continue
            share = ltv_shares[i] if ltv_shares is not None else spec.provenu_share
            comp_provenu = input.desired_provenu * share
            comp = _compute_component(spec, comp_provenu, input.tax_rate)
            comp_results.append((i, spec, comp))
            realkredit_kontant += comp.kontant
            if comp.par_capped:
                any_par_capped = True
        # Bank components: residual provenu if any realkredit was par-capped
        bank_specs = [s for s in alt.components if s.component == LoanComponent.BANK]
        total_bank_share = sum(s.provenu_share for s in bank_specs)
        for i, spec in enumerate(alt.components):
            if spec.component != LoanComponent.BANK:
                continue
            if any_par_capped and total_bank_share > _ZERO:
                bank_provenu = (input.desired_provenu - realkredit_kontant) * (
                    spec.provenu_share / total_bank_share
                )
            else:
                share = ltv_shares[i] if ltv_shares is not None else spec.provenu_share
                bank_provenu = input.desired_provenu * share
            comp_results.append(
                (i, spec, _compute_component(spec, bank_provenu, input.tax_rate))
            )
        # Restore original component order
        comp_results.sort(key=lambda pair: pair[0])
        comp_results: list[tuple[LoanSpec, LoanComponentResult]] = [
            (spec, comp) for _, spec, comp in comp_results
        ]

        # Aggregate
        total_hovedstol = sum(r.hovedstol for _, r in comp_results)
        total_kursvaerdi = sum(r.kursvaerdi for _, r in comp_results)
        total_udst = sum(r.udstedelsesomkostning for _, r in comp_results)
        total_kontant = sum(r.kontant for _, r in comp_results)
        ydelse_bs = sum(r.ydelse_before_tax for _, r in comp_results)
        ydelse_es = _monthly_payment_after_tax(
            comp_results, input.tax_rate, shocked=False
        )

        # Weighted-average ÅOP (by hovedstol)
        if total_hovedstol > _ZERO:
            aap = (
                sum(r.aap_before_tax * r.hovedstol for _, r in comp_results)
                / total_hovedstol
            )
        else:
            aap = _ZERO

        # Weighted-average kurs
        if total_hovedstol > _ZERO:
            gns_kurs = (
                sum(r.gns_kurs * r.hovedstol for _, r in comp_results) / total_hovedstol
            )
        else:
            gns_kurs = Decimal(100)

        alt_summaries.append(
            AlternativeSummary(
                label=alt.label,
                total_hovedstol=total_hovedstol,
                gns_kurs=gns_kurs,
                total_kursvaerdi=total_kursvaerdi,
                total_udstedelsesomkostning=total_udst,
                total_kontant=total_kontant,
                ydelse_before_tax=ydelse_bs,
                ydelse_after_tax=ydelse_es,
                aap_before_tax=aap,
                components=[r for _, r in comp_results],
            )
        )

        # Horizon
        scenarios = _horizon_scenarios(
            comp_results,
            horizon_years,
            input.tax_rate,
            input.rate_shocks,
            input.desired_provenu,
        )
        horizon_analyses.append(
            HorizonAnalysis(
                alternative_label=alt.label,
                scenarios=scenarios,
            )
        )

    return CalculatorResult(
        desired_provenu=input.desired_provenu,
        ejendomsvaerdi=input.ejendomsvaerdi,
        start_date=input.start_date,
        horizon_date=horizon_date,
        tax_rate=input.tax_rate,
        alternatives=alt_summaries,
        horizon_analyses=horizon_analyses,
    )


# ─── Amortization schedule (afdragstabel) ────────────────────────────


def amortization_schedule(
    input: CalculatorInput,
    alternative_index: int,
) -> AmortizationSchedule:  # type: ignore[name-defined]
    """Year-by-year amortization schedule for one alternative.

    Amortizes each component at its effective rate, aggregates by year.
    Returns yearly totals: ydelse, rente (before+after tax), afdrag, restgæld.
    """
    from .models import AmortizationSchedule, AmortizationYear

    alt = input.alternatives[alternative_index]
    ltv_shares = _ltv_shares(input, alt.components)

    # Build per-component monthly amortization lists
    # Each entry: (monthly amortization, ppy, bidrag_charge_per_period)
    comp_monthly: list[tuple[list[tuple[Decimal, Decimal, Decimal]], int, Decimal]] = []
    for i, spec in enumerate(alt.components):
        ppy = spec.payments_per_year
        share = ltv_shares[i] if ltv_shares is not None else spec.provenu_share
        comp_provenu = input.desired_provenu * share
        comp = _compute_component(spec, comp_provenu, input.tax_rate)
        n = int(
            (spec.maturity_years * Decimal(ppy)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        io_months = spec.interest_only_years * ppy

        # Build rate path for the full term (no shock)
        path = _rate_path(spec, _ZERO, n, ppy)

        if spec.loan_type == LoanType.T and spec.fixed_ydelse is not None:
            # T-lån: amortize with fixed payment; actual term may differ
            if spec.bidrag_model == "split":
                r = _periodic_rate(spec.rate, ppy)
            else:
                r = _periodic_rate(_effective_rate(spec), ppy)
            actual_n = _solve_for_n(comp.hovedstol, r, spec.fixed_ydelse)
            if actual_n < 0:
                actual_n = n
            monthly = list(
                _amortize(
                    comp.hovedstol,
                    path,
                    actual_n,
                    payment=spec.fixed_ydelse,
                    ppy=ppy,
                    bidragssats=spec.bidragssats,
                    bidrag_model=spec.bidrag_model,
                )
            )
        else:
            monthly = list(
                _amortize(
                    comp.hovedstol,
                    path,
                    n,
                    interest_only_months=io_months,
                    ppy=ppy,
                    bidragssats=spec.bidragssats,
                    bidrag_model=spec.bidrag_model,
                )
            )
        bidrag_charge = (
            spec.bidragssats * comp.hovedstol / Decimal(ppy)
            if spec.bidrag_model == "split"
            else _ZERO
        )
        comp_monthly.append((monthly, ppy, bidrag_charge))

    # n_years: max of nominal maturity and actual T-lån term (ceil to years)
    n_years = max(
        int(max(spec.maturity_years for spec in alt.components)),
        max((len(m) + p - 1) // p for m, p, _ in comp_monthly),
    )

    years: list[AmortizationYear] = []
    for year in range(1, n_years + 1):
        ydelse_y = _ZERO
        rente_y = _ZERO
        afdrag_y = _ZERO
        restgaeld_y = _ZERO

        for months, ppy, bidrag_charge in comp_monthly:
            start_idx = (year - 1) * ppy
            end_idx = start_idx + ppy
            num_periods = 0
            for m in range(start_idx, min(end_idx, len(months))):
                interest, principal, balance = months[m]
                ydelse_y += interest + principal
                rente_y += interest
                afdrag_y += principal
                num_periods += 1
            # Add bidrag charge for each period in this year (split mode)
            ydelse_y += bidrag_charge * Decimal(num_periods)
            # Sum remaining balances across all components (not just last)
            comp_end = min(end_idx, len(months)) - 1
            if comp_end >= 0:
                restgaeld_y += months[comp_end][2]

        years.append(
            AmortizationYear(
                year=year,
                ydelse=ydelse_y,
                rente=rente_y,
                afdrag=afdrag_y,
                restgaeld=restgaeld_y,
                rente_after_tax=rente_y * (_ONE - input.tax_rate),
            )
        )

    return AmortizationSchedule(
        alternative_label=alt.label,
        years=years,
    )
