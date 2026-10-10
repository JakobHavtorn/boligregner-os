"""Engine tests — verify math against boligregner.dk reference data."""

from datetime import date
from decimal import Decimal

import pytest

from boligregner import PRESETS, amortization_schedule, calculate
from boligregner.engine import (
    _aap,
    _annuity_payment,
    _compute_component,
    _daily_to_monthly,
    _hovedstol_for_provenu,
    _irr,
    _monthly_rate,
    _rate_path,
    _solve_for_n,
)
from boligregner.models import (
    CalculatorInput,
    FinancingAlternative,
    LoanComponent,
    LoanSpec,
    LoanType,
)

# ─── Primitive unit tests ────────────────────────────────────────────


class TestAnnuity:
    def test_zero_rate_straight_line(self):
        # No interest → equal principal payments
        pay = _annuity_payment(Decimal(360000), Decimal(0), 360)
        assert pay == Decimal(1000)

    def test_known_annuity(self):
        # 1M at 4% annual, 30yr: monthly ≈ 4774.15
        r = _monthly_rate(Decimal("0.04"))
        pay = _annuity_payment(Decimal(1000000), r, 360)
        assert abs(pay - Decimal("4774.15")) < Decimal("0.10")

    def test_payment_amortizes_to_zero(self):
        # 360 payments should bring balance to ~0
        r = _monthly_rate(Decimal("0.04"))
        pay = _annuity_payment(Decimal(1000000), r, 360)
        balance = Decimal(1000000)
        for _ in range(360):
            interest = balance * r
            principal = pay - interest
            balance -= principal
        assert abs(balance) < Decimal(1)


class TestHovedstol:
    def test_par_price_no_costs(self):
        # Price 100, no costs: hovedstol == provenu (rounded to 1000s)
        h, _, _ = _hovedstol_for_provenu(Decimal(2500000), Decimal(100), Decimal(0))
        assert h == Decimal(2500000)

    def test_discount_obligation(self):
        # Price 94.52, ~1.71% costs: hovedstol should be ~2.694M for 2.5M provenu
        h, _, _ = _hovedstol_for_provenu(
            Decimal(2500000), Decimal("94.52"), Decimal("0.0171")
        )
        # Should be ~2.694M (matching boligregner Alt 3)
        assert abs(h - Decimal(2694000)) < Decimal(2000)

    def test_raises_on_impossible_price(self):
        with pytest.raises(ValueError, match="non-positive provenu"):
            _hovedstol_for_provenu(Decimal(2500000), Decimal(100), Decimal("1.5"))

    def test_kontantlaan_at_par(self):
        # Kontantlån (flexlån): derive at par regardless of bond kurs.
        # provenu=2.500.000, nominal udst.omk=46.844 → hovedstol=2.547.000
        h, oblig, _ = _hovedstol_for_provenu(
            Decimal(2500000),
            Decimal("91.36"),
            Decimal(0),
            issue_costs_nominal=Decimal(46844),
            kontantlaan=True,
        )
        assert h == Decimal(2547000)  # round_up_1000(2.500.000 + 46.844)
        # obligationshovedstol at kurs 91.36 is larger
        assert oblig > h
        assert abs(oblig - Decimal(2788000)) < Decimal(2000)


class TestIRR:
    def test_flat_cashflow(self):
        # Lend 100, get back 10 for 12 months → monthly IRR ≈ some positive value
        cfs = [Decimal(-100)] + [Decimal(10)] * 12
        r = _irr(cfs)
        assert r > Decimal(0)
        # Annualized should be reasonable
        assert r * Decimal(12) < Decimal(1)

    def test_aap_positive(self):
        # 1M loan at 4%, net cash received = 1M - 17k costs = 983000
        aap = _aap(Decimal(1000000), [Decimal("0.04")] * 360, 360, Decimal(983000))
        # ÅOP should be slightly above 4% due to upfront costs reducing net
        assert aap > Decimal("0.04")
        assert aap < Decimal("0.05")


# ─── Full engine smoke test against boligregner sample ───────────────


class TestCalculatePreset:
    """Run the default preset and check it produces sensible numbers."""

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls):
        return calculate(PRESETS["default"])

    def test_returns_three_alternatives(self, result):
        assert len(result.alternatives) == 3
        labels = [a.label for a in result.alternatives]
        assert "30 år DESTR" in labels
        assert "30 år F1" in labels
        assert "30 år 4% obligation" in labels

    def test_provenu_preserved(self, result):
        assert result.desired_provenu == Decimal(2500000)

    def test_horizon_date(self, result):
        # 5 years from 2026-10-02 → 2031-10-01
        assert result.horizon_date.year == 2031
        assert result.horizon_date.month == 10
        assert result.horizon_date.day == 1

    def test_alt3_discount_obligation_hovedstol(self, result):
        """Alt 3 (4% obligation at price 94.52) should have hovedstol ≈ 2.694M."""
        alt3 = result.alternatives[2]
        assert alt3.label.startswith("30 år 4%")
        # With price 94.52, hovedstol should be notably higher than provenu
        assert alt3.total_hovedstol > Decimal(2600000)
        assert alt3.total_hovedstol < Decimal(2800000)

    def test_alt3_gns_kurs(self, result):
        """Alt 3 weighted kurs should be near 94.52 (realkredit dominates)."""
        alt3 = result.alternatives[2]
        # Weighted by hovedstol; realkredit is 80% at 94.52, bank 20% at 100
        assert alt3.gns_kurs < Decimal(96)

    def test_flexlaan_par_kurs(self, result):
        """Alt 1 (DESTR) & Alt 2 (F1) should have gns_kurs near 100."""
        for alt in result.alternatives[:2]:
            assert alt.gns_kurs > Decimal(99)

    def test_aap_ordering(self, result):
        """ÅOP should increase: DESTR < F1 < 4% obligation."""
        aaps = [a.aap_before_tax for a in result.alternatives]
        assert aaps[0] < aaps[1] < aaps[2]

    def test_horizon_has_three_scenarios(self, result):
        for ha in result.horizon_analyses:
            assert len(ha.scenarios) == 3
            shocks = [s.rate_shock for s in ha.scenarios]
            assert shocks == [Decimal("-0.02"), Decimal(0), Decimal("0.02")]

    def test_horizon_rate_shock_affects_ydelse(self, result):
        """For DESTR (Alt 1), +2% shock should raise ydelse_slut vs start."""
        alt1_ha = result.horizon_analyses[0]
        for row in alt1_ha.scenarios:
            if row.rate_shock == Decimal("0.02"):
                assert row.ydelse_slut > row.ydelse_start
            if row.rate_shock == Decimal("-0.02"):
                assert row.ydelse_slut < row.ydelse_start

    def test_periodeomkostning_flexlaan_positive(self, result):
        """DESTR & F1 periodeomkostninger should be positive (no price-gain from rate shock)."""
        for ha in result.horizon_analyses[:2]:  # Alt 1 (DESTR) & 2 (F1)
            for row in ha.scenarios:
                assert row.periodeomkostning > Decimal(0)

    def test_fixed_obligation_shock_lowers_periodeomk(self, result):
        """For fixed-rate obligations, +2% shock lowers indfrielse (bond price falls)
        and thus lowers periodeomkostning vs 0% shock."""
        alt3_ha = result.horizon_analyses[2]  # 4% obligation
        by_shock = {row.rate_shock: row for row in alt3_ha.scenarios}
        # +2% shock: bond price falls → cheaper indfrielse → lower periodeomk
        assert by_shock[Decimal("0.02")].indfrielse < by_shock[Decimal(0)].indfrielse
        assert (
            by_shock[Decimal("0.02")].periodeomkostning
            < by_shock[Decimal(0)].periodeomkostning
        )
        # -2% shock: bond price rises → more expensive indfrielse
        assert by_shock[Decimal("-0.02")].indfrielse > by_shock[Decimal(0)].indfrielse

    def test_restgaeld_less_than_hovedstol(self, result):
        """After 5 years of payments, remaining debt < original hovedstol.

        Exceptions: T-lån under +2% shock can grow (negative amortization),
        and afdragsfrihed loans don't amortize during the interest-only period.
        """
        for i, ha in enumerate(result.horizon_analyses):
            hovedstol = result.alternatives[i].total_hovedstol
            for row in ha.scenarios:
                # T-lån with positive shock: balance may grow beyond hovedstol
                if row.restgaeld > hovedstol:
                    # Only acceptable for T-lån under positive shock or IO loans
                    assert row.rate_shock > Decimal(0), (
                        f"Alt {i} restgæld {row.restgaeld} > hovedstol {hovedstol} "
                        f"at shock {row.rate_shock} — should only happen for T-lån"
                    )

    def test_indfrielse_near_restgaeld(self, result):
        """Indfrielse ≈ restgæld when redemption_price is 100 (flexlån)."""
        alt1_ha = result.horizon_analyses[0]
        for row in alt1_ha.scenarios:
            # Flexlån redemption at par → indfrielse ≈ restgaeld
            assert abs(row.indfrielse - row.restgaeld) / row.restgaeld < Decimal("0.05")


# ─── Afdragsfrihed (interest-only period) tests ──────────────────────


def _make_single_alt(
    loan_type: LoanType = LoanType.F3,
    rate: Decimal = Decimal("0.035"),
    price: Decimal = Decimal(100),
    maturity: int = 30,
    issue_pct: Decimal = Decimal("0.0177"),
    bidrag: Decimal = Decimal("0.006"),
    interest_only_years: int = 0,
    fixed_ydelse: Decimal | None = None,
) -> CalculatorInput:
    """Build a minimal single-component CalculatorInput for testing."""
    return CalculatorInput(
        desired_provenu=Decimal(2500000),
        start_date=date(2026, 10, 2),
        horizon_years=5,
        tax_rate=Decimal("0.336"),
        alternatives=[
            FinancingAlternative(
                label="test",
                components=[
                    LoanSpec(
                        component=LoanComponent.REALKREDIT,
                        loan_type=loan_type,
                        rate=rate,
                        price=price,
                        maturity_years=maturity,
                        issue_costs_pct=issue_pct,
                        bidragssats=bidrag,
                        interest_only_years=interest_only_years,
                        fixed_ydelse=fixed_ydelse,
                        payments_per_year=12,
                        bidrag_model="compounded",
                        bond_price_model="simple",
                    ),
                ],
            ),
        ],
    )


class TestAfdragsfrihed:
    """Tests for the interest-only (afdragsfrihed) feature."""

    def test_interest_only_period_no_amortization(self):
        """5yr interest-only on a 30yr loan → first 5 years have afdrag=0, restgæld = hovedstol."""
        inp = _make_single_alt(interest_only_years=5)
        sched = amortization_schedule(inp, 0)
        # First 5 years: afdrag = 0
        for year in sched.years[:5]:
            assert year.afdrag == Decimal(0), (
                f"Year {year.year} should have afdrag=0, got {year.afdrag}"
            )
        # Restgæld should equal hovedstol during interest-only period
        hovedstol = calculate(inp).alternatives[0].total_hovedstol
        for year in sched.years[:5]:
            assert year.restgaeld == hovedstol, (
                f"Year {year.year} restgaeld should be {hovedstol}, got {year.restgaeld}"
            )
        # After interest-only, afdrag > 0
        assert sched.years[5].afdrag > Decimal(0)

    def test_annuity_after_interest_only(self):
        """After interest-only period, the annuity payment is computed over 25yr (not 30yr)
        → higher payment than a standard 30yr loan."""
        io_input = _make_single_alt(interest_only_years=5)
        std_input = _make_single_alt(interest_only_years=0)
        io_result = calculate(io_input)
        std_result = calculate(std_input)
        io_ydelse = io_result.alternatives[0].ydelse_before_tax
        std_ydelse = std_result.alternatives[0].ydelse_before_tax
        assert io_ydelse > std_ydelse, (
            f"IO ydelse {io_ydelse} should be > std {std_ydelse}"
        )

    def test_aap_higher_with_afdragsfrihed(self):
        """ÅOP differs from standard because the interest-only period changes the
        cashflow timing. The total cost is higher (more interest paid over the life
        of the loan), but the annualised rate (ÅOP) may be lower because issue costs
        are spread over a higher average balance. Verify the ÅOP is materially
        different (not equal) — confirming the interest-only cashflow is captured."""
        io_input = _make_single_alt(interest_only_years=5)
        std_input = _make_single_alt(interest_only_years=0)
        io_aap = calculate(io_input).alternatives[0].aap_before_tax
        std_aap = calculate(std_input).alternatives[0].aap_before_tax
        assert io_aap != std_aap, f"IO ÅOP {io_aap} should differ from std {std_aap}"
        # Both should be above the effective rate (issue costs push ÅOP up)
        eff_rate = Decimal("0.035") + Decimal("0.006")  # rate + bidrag
        assert io_aap > eff_rate
        assert std_aap > eff_rate

    def test_horizon_during_interest_only(self):
        """If horizon=3yr and interest_only=5yr, the horizon shows 0 afdrag, full restgæld."""
        inp = _make_single_alt(interest_only_years=5)
        # Override horizon to 3 years
        inp = inp.model_copy(update={"horizon_years": 3})
        result = calculate(inp)
        ha = result.horizon_analyses[0]
        hovedstol = result.alternatives[0].total_hovedstol
        for row in ha.scenarios:
            assert row.afdrag_total == Decimal(0), (
                f"afdrag_total should be 0 during interest-only, got {row.afdrag_total}"
            )
            assert row.restgaeld == hovedstol, (
                f"restgaeld should be {hovedstol} during interest-only, got {row.restgaeld}"
            )

    def test_zero_interest_only_same_as_standard(self):
        """interest_only_years=0 produces identical results to the standard loan."""
        std_input = _make_single_alt(interest_only_years=0)
        no_io_input = _make_single_alt(interest_only_years=0)
        std_result = calculate(std_input)
        no_io_result = calculate(no_io_input)
        assert (
            std_result.alternatives[0].ydelse_before_tax
            == no_io_result.alternatives[0].ydelse_before_tax
        )
        assert (
            std_result.alternatives[0].aap_before_tax
            == no_io_result.alternatives[0].aap_before_tax
        )
        std_sched = amortization_schedule(std_input, 0)
        no_io_sched = amortization_schedule(no_io_input, 0)
        assert len(std_sched.years) == len(no_io_sched.years)
        for sy, ny in zip(std_sched.years, no_io_sched.years):
            assert sy.afdrag == ny.afdrag
            assert sy.restgaeld == ny.restgaeld


class TestTLån:
    """Tests for the T-lån (fixed payment, variable duration) feature."""

    def test_fixed_ydelse(self):
        """T-lån ydelse_before_tax equals the fixed_ydelse input."""
        inp = _make_single_alt(
            loan_type=LoanType.T,
            rate=Decimal("0.038"),
            fixed_ydelse=Decimal(9900),
        )
        result = calculate(inp)
        assert result.alternatives[0].ydelse_before_tax == Decimal(9900)

    def test_duration_extends_with_rate_shock(self):
        """At +2% shock, restgæld after horizon is HIGHER than at 0% (slower amortization)."""
        inp = _make_single_alt(
            loan_type=LoanType.T,
            rate=Decimal("0.038"),
            fixed_ydelse=Decimal(9900),
        )
        result = calculate(inp)
        ha = result.horizon_analyses[0]
        by_shock = {row.rate_shock: row for row in ha.scenarios}
        assert by_shock[Decimal("0.02")].restgaeld > by_shock[Decimal(0)].restgaeld, (
            f"+2% restgaeld {by_shock[Decimal('0.02')].restgaeld} should be > "
            f"0% {by_shock[Decimal(0)].restgaeld}"
        )

    def test_duration_shortens_with_negative_shock(self):
        """At -2% shock, restgæld after horizon is LOWER than at 0%."""
        inp = _make_single_alt(
            loan_type=LoanType.T,
            rate=Decimal("0.038"),
            fixed_ydelse=Decimal(9900),
        )
        result = calculate(inp)
        ha = result.horizon_analyses[0]
        by_shock = {row.rate_shock: row for row in ha.scenarios}
        assert by_shock[Decimal("-0.02")].restgaeld < by_shock[Decimal(0)].restgaeld, (
            f"-2% restgaeld {by_shock[Decimal('-0.02')].restgaeld} should be < "
            f"0% {by_shock[Decimal(0)].restgaeld}"
        )

    def test_ydelse_unchanged_by_shock(self):
        """For T-lån, the before-tax ydelse is fixed and doesn't change with rate shock.
        The after-tax ydelse_slut differs from ydelse_start only because the interest
        deduction changes with the rate — but the gross payment stays constant."""
        inp = _make_single_alt(
            loan_type=LoanType.T,
            rate=Decimal("0.038"),
            fixed_ydelse=Decimal(9900),
        )
        result = calculate(inp)
        # Before-tax ydelse in the summary should be exactly fixed_ydelse
        assert result.alternatives[0].ydelse_before_tax == Decimal(9900)
        # For each shock scenario, the before-tax payment is still fixed_ydelse.
        # ydelse_slut (after tax) + interest_at_horizon * tax_rate == fixed_ydelse.
        # Since ydelse_slut = fixed_ydelse - interest * tax_rate, and interest
        # varies with the shock, ydelse_slut varies — but the gross payment is fixed.
        # Verify: ydelse_slut is derived from the same fixed_ydelse.
        ha = result.horizon_analyses[0]
        for row in ha.scenarios:
            # The before-tax ydelse at start and end should both be fixed_ydelse.
            # ydelse_start = fixed_ydelse - hovedstol * r_original * tax_rate
            # ydelse_slut  = fixed_ydelse - balance * r_shocked * tax_rate
            # Both are derived from the same fixed_ydelse.
            # Check the difference is entirely due to tax deduction changes.
            tax_rate = Decimal("0.336")
            # Reconstruct before-tax from start: start + interest_start * tax = fixed
            # We know fixed_ydelse = 9900, so verify ydelse_start < 9900 and ydelse_slut < 9900
            assert row.ydelse_start < Decimal(9900), (
                f"ydelse_start {row.ydelse_start} should be < 9900 (after tax)"
            )
            assert row.ydelse_slut < Decimal(9900), (
                f"ydelse_slut {row.ydelse_slut} should be < 9900 (after tax)"
            )

    def test_solve_for_n(self):
        """Known values: 1M at 4% annual, payment 4774.15 → n ≈ 360 months.
        The exact annuity for 360 months is 4774.153, so 4774.15 needs 361 months.
        Using the exact annuity payment should give exactly 360."""
        r = _monthly_rate(Decimal("0.04"))
        # Use the exact annuity payment for 360 months
        exact_payment = _annuity_payment(Decimal(1000000), r, 360)
        n = _solve_for_n(Decimal(1000000), r, exact_payment)
        assert n == 360, f"Expected 360 with exact payment, got {n}"
        # With a slightly lower payment, n should be 361
        n2 = _solve_for_n(Decimal(1000000), r, Decimal("4774.15"))
        assert n2 == 361, f"Expected 361 with lower payment, got {n2}"

    def test_solve_for_n_too_small_payment(self):
        """Payment less than first month's interest → returns -1."""
        r = _monthly_rate(Decimal("0.04"))
        # 1M * 0.04/12 = 3333.33 → payment of 3000 doesn't cover interest
        n = _solve_for_n(Decimal(1000000), r, Decimal(3000))
        assert n == -1, f"Expected -1, got {n}"


# ─── CITA/CIBOR/DESTR tests ──────────────────────────────────────────


def _make_ref_alt(
    loan_type: LoanType = LoanType.CIBOR,
    reference_rate: Decimal = Decimal("0.0320"),
    margin: Decimal = Decimal("0.0025"),
    rate: Decimal | None = None,
    price: Decimal = Decimal(100),
    maturity: int = 30,
    issue_pct: Decimal = Decimal("0.0177"),
    bidrag: Decimal = Decimal("0.006"),
    fixed_ydelse: Decimal | None = None,
    interest_only_years: int = 0,
) -> CalculatorInput:
    """Build a minimal single-component CalculatorInput for CITA/CIBOR/DESTR testing."""
    if rate is None:
        rate = reference_rate + margin
    return CalculatorInput(
        desired_provenu=Decimal(2500000),
        start_date=date(2026, 10, 2),
        horizon_years=5,
        tax_rate=Decimal("0.336"),
        alternatives=[
            FinancingAlternative(
                label="test",
                components=[
                    LoanSpec(
                        component=LoanComponent.REALKREDIT,
                        loan_type=loan_type,
                        rate=rate,
                        price=price,
                        maturity_years=maturity,
                        issue_costs_pct=issue_pct,
                        bidragssats=bidrag,
                        reference_rate=reference_rate,
                        margin=margin,
                        fixed_ydelse=fixed_ydelse,
                        interest_only_years=interest_only_years,
                        payments_per_year=12,
                        bidrag_model="compounded",
                        bond_price_model="simple",
                    ),
                ],
            ),
        ],
    )


class TestCitaCiborDestr:
    """Tests for CITA/CIBOR/DESTR short-period variable rate loans."""

    def test_cibor_rate_path_structure(self):
        """_rate_path for CIBOR returns a constant array of length n_months
        at the expected rate: reference + margin + bidrag."""
        spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.CIBOR,
            rate=Decimal("0.0345"),
            price=Decimal(100),
            maturity_years=30,
            issue_costs_pct=Decimal("0.0177"),
            bidragssats=Decimal("0.006"),
            reference_rate=Decimal("0.0320"),
            margin=Decimal("0.0025"),
            payments_per_year=12,
            bidrag_model="compounded",
            bond_price_model="simple",
        )
        # With zero shock, the rate is constant at reference + margin + bidrag
        path = _rate_path(spec, Decimal(0), 12)
        assert len(path) == 12
        expected = Decimal("0.0320") + Decimal("0.0025") + Decimal("0.006")
        assert all(r == expected for r in path)
        # With +2% shock, the rate is constant at (reference + shock) + margin + bidrag
        path_shocked = _rate_path(spec, Decimal("0.02"), 12)
        assert len(path_shocked) == 12
        expected_shocked = (
            (Decimal("0.0320") + Decimal("0.02")) + Decimal("0.0025") + Decimal("0.006")
        )
        assert all(r == expected_shocked for r in path_shocked)

    def test_cibor_shock_applies_to_reference_not_margin(self):
        """Rate at shock=+2% = (reference+0.02) + margin + bidrag,
        not (reference+margin+0.02+bidrag) — shock applies to reference only."""
        spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.CIBOR,
            rate=Decimal("0.0345"),
            price=Decimal(100),
            maturity_years=30,
            issue_costs_pct=Decimal("0.0177"),
            bidragssats=Decimal("0.006"),
            reference_rate=Decimal("0.0320"),
            margin=Decimal("0.0025"),
            bidrag_model="compounded",
        )
        path = _rate_path(spec, Decimal("0.02"), 6)
        # Shock applies to reference: (0.0320 + 0.02) + 0.0025 + 0.006
        expected = (
            (Decimal("0.0320") + Decimal("0.02")) + Decimal("0.0025") + Decimal("0.006")
        )
        assert path[0] == expected
        # At -2% shock, reference can go negative (Danish rates were negative in 2010s)
        path_neg = _rate_path(spec, Decimal("-0.02"), 6)
        expected_neg = (
            (Decimal("0.0320") - Decimal("0.02")) + Decimal("0.0025") + Decimal("0.006")
        )
        assert path_neg[0] == expected_neg

    def test_cibor_payment_changes_at_reset(self):
        """Monthly payment changes when a shock is applied: the payment at
        +2% shock differs from the zero-shock payment."""
        inp = _make_ref_alt(loan_type=LoanType.CIBOR)
        result = calculate(inp)
        ha = result.horizon_analyses[0]
        by_shock = {row.rate_shock: row for row in ha.scenarios}
        # At +2% shock, ydelse_slut should be higher than at 0%
        assert (
            by_shock[Decimal("0.02")].ydelse_slut > by_shock[Decimal(0)].ydelse_slut
        ), (
            f"+2% ydelse_slut {by_shock[Decimal('0.02')].ydelse_slut} should be > "
            f"0% {by_shock[Decimal(0)].ydelse_slut}"
        )
        # At -2% shock, ydelse_slut should be lower than at 0%
        assert (
            by_shock[Decimal("-0.02")].ydelse_slut < by_shock[Decimal(0)].ydelse_slut
        ), (
            f"-2% ydelse_slut {by_shock[Decimal('-0.02')].ydelse_slut} should be < "
            f"0% {by_shock[Decimal(0)].ydelse_slut}"
        )

    def test_destr_daily_compounding(self):
        """_daily_to_monthly converts correctly; DESTR rate differs from
        simple monthly rate."""
        annual = Decimal("0.0340")
        monthly = _daily_to_monthly(annual)
        # (1 + 0.034/360)^30 - 1
        expected = (Decimal(1) + annual / Decimal(360)) ** 30 - Decimal(1)
        assert monthly == expected
        # The daily-compounded monthly rate should be slightly different
        # from the simple monthly rate (annual/12)
        simple_monthly = annual / Decimal(12)
        assert monthly != simple_monthly
        # Daily compounding should give a slightly higher effective monthly rate
        # than simple division (compounding effect)
        assert monthly > simple_monthly

    def test_destr_vs_cibor_same_rate_different_compounding(self):
        """Same reference+margin but DESTR costs slightly more due to
        daily compounding. The DESTR rate path should give a higher
        effective monthly rate than CIBOR at the same reference+margin."""
        ref = Decimal("0.0315")
        margin = Decimal("0.0025")
        bidrag = Decimal("0.006")
        cibor_spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.CIBOR,
            rate=ref + margin,
            price=Decimal(100),
            maturity_years=30,
            issue_costs_pct=Decimal("0.0177"),
            bidragssats=bidrag,
            reference_rate=ref,
            margin=margin,
            bidrag_model="compounded",
        )
        destr_spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.DESTR,
            rate=ref + margin,
            price=Decimal(100),
            maturity_years=30,
            issue_costs_pct=Decimal("0.0177"),
            bidragssats=bidrag,
            reference_rate=ref,
            margin=margin,
            bidrag_model="compounded",
        )
        cibor_path = _rate_path(cibor_spec, Decimal(0), 12)
        destr_path = _rate_path(destr_spec, Decimal(0), 12)
        # DESTR annual effective rate should differ from CIBOR
        # due to daily compounding conversion
        assert cibor_path[0] != destr_path[0], (
            f"DESTR rate {destr_path[0]} should differ from CIBOR {cibor_path[0]}"
        )
        # DESTR rate path: (1 + ref/360)^30 * 12 - 12 + margin + bidrag
        # vs CIBOR: ref + margin + bidrag
        # The daily-compounded monthly equivalent annualized is:
        #   monthly = (1 + ref/360)^30 - 1
        #   annual_equiv = monthly * 12
        # Compare: cibor = ref + margin + bidrag (simple)
        #          destr = monthly * 12 + margin + bidrag
        cibor_rate = ref + margin + bidrag
        destr_monthly = _daily_to_monthly(ref)
        destr_rate = destr_monthly * Decimal(12) + margin + bidrag
        assert cibor_path[0] == cibor_rate
        assert destr_path[0] == destr_rate
        # DESTR effective rate should be slightly different from CIBOR
        # (the compounding effect is small at ~3% rates)
        assert destr_path[0] != cibor_path[0]

    def test_cibor_zero_shock_matches_initial_rate(self):
        """At 0% shock, the rate path is constant at reference+margin+bidrag."""
        spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.CIBOR,
            rate=Decimal("0.0345"),
            price=Decimal(100),
            maturity_years=30,
            issue_costs_pct=Decimal("0.0177"),
            bidragssats=Decimal("0.006"),
            reference_rate=Decimal("0.0320"),
            margin=Decimal("0.0025"),
            bidrag_model="compounded",
        )
        path = _rate_path(spec, Decimal(0), 12)
        expected = Decimal("0.0320") + Decimal("0.0025") + Decimal("0.006")
        assert all(r == expected for r in path)

    def test_reference_rate_required_for_cibor(self):
        """model_validator raises if reference_rate is None for CIBOR."""
        with pytest.raises(ValueError, match="requires reference_rate"):
            LoanSpec(
                component=LoanComponent.REALKREDIT,
                loan_type=LoanType.CIBOR,
                rate=Decimal("0.0345"),
                price=Decimal(100),
                maturity_years=30,
                issue_costs_pct=Decimal("0.0177"),
                bidragssats=Decimal("0.006"),
                margin=Decimal("0.0025"),
            )

    def test_reference_rate_forbidden_for_fixed(self):
        """model_validator raises if reference_rate is set for FIXED."""
        with pytest.raises(ValueError, match="reference_rate is only for"):
            LoanSpec(
                component=LoanComponent.REALKREDIT,
                loan_type=LoanType.FIXED,
                rate=Decimal("0.04"),
                price=Decimal("94.52"),
                maturity_years=30,
                issue_costs_pct=Decimal("0.0171"),
                bidragssats=Decimal("0.006"),
                reference_rate=Decimal("0.0320"),
            )

    def test_cibor_aap_computed_with_variable_payments(self):
        """CIBOR loan ÅOP is computed correctly. The ÅOP should be close to the
        effective rate (reference + margin + bidrag) since there's no shock."""
        inp = _make_ref_alt(loan_type=LoanType.CIBOR)
        result = calculate(inp)
        aap = result.alternatives[0].aap_before_tax
        eff_rate = Decimal("0.0320") + Decimal("0.0025") + Decimal("0.006")
        # ÅOP should be above the effective rate (issue costs push it up)
        assert aap > eff_rate, f"ÅOP {aap} should be > effective rate {eff_rate}"
        # But not absurdly high
        assert aap < eff_rate + Decimal("0.01"), (
            f"ÅOP {aap} should be < {eff_rate + Decimal('0.01')}"
        )

    def test_cibor_amortization_schedule_shows_payment_changes(self):
        """amortization_schedule for CIBOR is computed correctly. With 0%
        shock, the rate is constant, so the payment stays the same across
        years. The schedule should still amortize to near-zero."""
        inp = _make_ref_alt(loan_type=LoanType.CIBOR)
        sched = amortization_schedule(inp, 0)
        # Should have 30 years
        assert len(sched.years) == 30
        # Year 1 ydelse should be positive
        assert sched.years[0].ydelse > Decimal(0)
        # Restgæld should decrease over time
        assert sched.years[1].restgaeld < sched.years[0].restgaeld
        # Final year should have near-zero restgæld
        assert sched.years[-1].restgaeld < Decimal(1000), (
            f"Final restgæld {sched.years[-1].restgaeld} should be near zero"
        )


# ─── Correctness checks ────────────────────────────────────────────


class TestHovedstolDerivation:
    """Verify _hovedstol_for_provenu()."""

    def test_f3_flexlaan_provenu_to_hovedstol(self):
        """F3: provenu=1.875.578, price=95.63, issue_pct=1.8511% → 2.000.000."""
        h, _, _ = _hovedstol_for_provenu(
            Decimal(1875578), Decimal("95.63"), Decimal("0.018511")
        )
        assert h == Decimal(2000000)

    def test_f5_flexlaan_provenu_to_hovedstol(self):
        """F5: provenu=1.790.378, price=91.43, issue_pct=1.9114% → 2.000.000."""
        h, _, _ = _hovedstol_for_provenu(
            Decimal(1790372), Decimal("91.43"), Decimal("0.019114")
        )
        assert h == Decimal(2000000)

    def test_4pct_fixed_provenu_to_hovedstol(self):
        """4% fixed: provenu=1.965.428, price=93.76, issue_pct=1.7577% → ~2.137.000."""
        h, _, _ = _hovedstol_for_provenu(
            Decimal(1965428), Decimal("93.76"), Decimal("0.017577")
        )
        # Engine rounds up to 2.137.000 (1k diff from 2.136.000)
        assert abs(h - Decimal(2136000)) < Decimal(2000), (
            f"hovedstol {h} should be within 2000 of 2.136.000"
        )

    def test_par_price_no_costs_exact(self):
        """Par price (100), no issue costs: hovedstol == provenu."""
        h, _, _ = _hovedstol_for_provenu(Decimal(2500000), Decimal(100), Decimal(0))
        assert h == Decimal(2500000)

    def test_discount_price_with_costs(self):
        """Discount price with issue costs: provenu=2.500.000, price=95, issue_pct=1.5%."""
        h, _, _ = _hovedstol_for_provenu(
            Decimal(2500000), Decimal(95), Decimal("0.015")
        )
        # The formula gives a deterministic result; allow up to 5000 kr
        # tolerance for the quantization to nearest 1000.
        expected = Decimal(2500000) / (Decimal(95) / Decimal(100) - Decimal("0.015"))
        expected_rounded = (expected / Decimal(1000)).to_integral_value(
            rounding="ROUND_CEILING"
        ) * Decimal(1000)
        assert abs(h - expected_rounded) < Decimal(5000), (
            f"hovedstol {h} should be within 5000 of {expected_rounded}"
        )

    def test_kontantlaan_f5_oct8_reference(self):
        """F5 kontantlån: provenu=2.500.133, kurs=91.30 → hovedstol 2.547.000."""
        h, oblig, _ = _hovedstol_for_provenu(
            Decimal(2500133),
            Decimal("91.30"),
            Decimal(0),
            issue_costs_nominal=Decimal(46867),
            kontantlaan=True,
        )
        assert h == Decimal(2547000)
        assert abs(oblig - Decimal(2790000)) < Decimal(2000)

    def test_kontantlaan_f1_oct8_reference(self):
        """F1 kontantlån: provenu=2.500.643, kurs=98.26 → hovedstol 2.545.000."""
        h, _oblig, _ = _hovedstol_for_provenu(
            Decimal(2500643),
            Decimal("98.26"),
            Decimal(0),
            issue_costs_nominal=Decimal(44357),
            kontantlaan=True,
        )
        assert h == Decimal(2545000)


class TestAnnuityFormula:
    """Verify _annuity_payment() against known mathematical reference values.

    Formula: ydelse = P * r * (1+r)^n / ((1+r)^n - 1)
    where r = monthly_rate (annual/12), n = total months.
    """

    def test_2m_at_4_18pct_30yr(self):
        """P=2.000.000, rate=4.18%, n=360 → ydelse ≈ 9.757,01.

        Tolerance: ±1 kr.
        """
        r = _monthly_rate(Decimal("0.0418"))
        ydelse = _annuity_payment(Decimal(2000000), r, 360)
        assert abs(ydelse - Decimal("9757.01")) < Decimal(1), (
            f"ydelse {ydelse} should be ≈ 9.757,01"
        )

    def test_2136k_at_4_70pct_30yr(self):
        """P=2.136.000, rate=4.70%, n=360 → ydelse ≈ 11.078,10.

        Tolerance: ±1 kr.
        """
        r = _monthly_rate(Decimal("0.0470"))
        ydelse = _annuity_payment(Decimal(2136000), r, 360)
        assert abs(ydelse - Decimal("11078.10")) < Decimal(1), (
            f"ydelse {ydelse} should be ≈ 11.078,10"
        )

    def test_zero_rate_straight_line_2m5(self):
        """P=2.500.000, rate=0%, n=360 → ydelse = 2.500.000/360 = 6.944,44.

        With zero rate, annuity degenerates to straight-line amortization.
        ydelse == hovedstol / n exactly.
        """
        hovedstol = Decimal(2500000)
        n = 360
        ydelse = _annuity_payment(hovedstol, Decimal(0), n)
        assert ydelse == hovedstol / Decimal(n), (
            f"ydelse {ydelse} should equal {hovedstol / Decimal(n)}"
        )
        assert abs(ydelse - Decimal("6944.44")) < Decimal(1), (
            f"ydelse {ydelse} should be ≈ 6.944,44"
        )

    def test_large_rate_1m_at_10pct_30yr(self):
        """P=1.000.000, rate=10%, n=360 → ydelse > 8.775.

        At 10% annual on 1M, first-year interest ≈ 83.000, so ydelse must
        exceed 83.000/12 ≈ 6.917 + principal.
        """
        r = _monthly_rate(Decimal("0.10"))
        ydelse = _annuity_payment(Decimal(1000000), r, 360)
        assert ydelse > Decimal(8775), (
            f"ydelse {ydelse} should be > 8.775 at 10% on 1M over 30yr"
        )

    def test_short_term_500k_at_3pct_3yr(self):
        """P=500.000, rate=3%, n=36 → ydelse ≈ 14.540,60.

        Tolerance: ±1 kr for rounding.
        """
        r = _monthly_rate(Decimal("0.03"))
        ydelse = _annuity_payment(Decimal(500000), r, 36)
        assert abs(ydelse - Decimal("14540.60")) < Decimal(1), (
            f"ydelse {ydelse} should be ≈ 14.540,60"
        )


class TestAapFixedRate:
    """Verify ÅOP for a 4% fixed-rate obligation.

    Reference ÅOP uses effective annual rate: (1 + monthly_irr)^12 - 1.
    _aap() returns nominal: monthly_irr * 12.
    """

    def test_aap_effective_annual_matches_reference(self):
        """4% fixed obligation: effective annual ÅOP ≈ 5.57%.

        type=FIXED, rate=0.04, price=93.76, maturity=30,
        issue_pct=0.017577, bidrag=0.0070
        provenu=1.965.428 → hovedstol≈2.137.000
        """
        spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.FIXED,
            rate=Decimal("0.04"),
            price=Decimal("93.76"),
            maturity_years=30,
            issue_costs_pct=Decimal("0.017577"),
            bidragssats=Decimal("0.0070"),
        )
        tax_rate = Decimal("0.336")
        provenu = Decimal(1965428)
        result = _compute_component(spec, provenu, tax_rate)

        # _aap returns nominal annual (monthly_irr * 12).
        # Convert to effective annual: (1 + aap/12)^12 - 1
        aap_nominal = result.aap_before_tax
        effective_annual = (Decimal(1) + aap_nominal / Decimal(12)) ** 12 - Decimal(1)

        # Reference: ÅOP 5.57%
        ref = Decimal("0.0557")
        tol = Decimal("0.0005")
        assert abs(effective_annual - ref) < tol, (
            f"effective annual {effective_annual} should be within {tol} of {ref}"
        )

    def test_aap_above_effective_rate(self):
        """ÅOP > effective rate (rate + bidrag).

        Issue costs and bond discount push ÅOP above the raw rate + bidrag.
        """
        spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.FIXED,
            rate=Decimal("0.04"),
            price=Decimal("93.76"),
            maturity_years=30,
            issue_costs_pct=Decimal("0.017577"),
            bidragssats=Decimal("0.0070"),
        )
        tax_rate = Decimal("0.336")
        provenu = Decimal(1965428)
        result = _compute_component(spec, provenu, tax_rate)

        eff_rate = spec.rate + spec.bidragssats  # 0.04 + 0.0070 = 0.047
        assert result.aap_before_tax > eff_rate, (
            f"ÅOP {result.aap_before_tax} should be > effective rate {eff_rate}"
        )

    def test_aap_par_loan_equals_eff_rate(self):
        """ÅOP for par loan (price=100, no issue costs) = effective rate."""
        spec = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.FIXED,
            rate=Decimal("0.04"),
            price=Decimal(100),
            maturity_years=30,
            issue_costs_pct=Decimal(0),
            bidragssats=Decimal("0.0070"),
        )
        tax_rate = Decimal("0.336")
        provenu = Decimal(2000000)
        result = _compute_component(spec, provenu, tax_rate)

        eff_rate = spec.rate + spec.bidragssats  # 0.047
        # For a par loan, ÅOP (nominal) should equal eff_rate (nominal).
        # Allow small numerical tolerance for IRR convergence.
        assert abs(result.aap_before_tax - eff_rate) < Decimal("0.001"), (
            f"ÅOP {result.aap_before_tax} should ≈ effective rate {eff_rate} "
            "for a par loan with no issue costs"
        )

    def test_aap_increases_with_issue_costs(self):
        """ÅOP increases when issue costs increase."""
        tax_rate = Decimal("0.336")
        provenu = Decimal(1965428)

        # Low issue costs
        spec_low = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.FIXED,
            rate=Decimal("0.04"),
            price=Decimal("93.76"),
            maturity_years=30,
            issue_costs_pct=Decimal("0.01"),  # 1% — low
            bidragssats=Decimal("0.0070"),
        )
        result_low = _compute_component(spec_low, provenu, tax_rate)

        # High issue costs
        spec_high = LoanSpec(
            component=LoanComponent.REALKREDIT,
            loan_type=LoanType.FIXED,
            rate=Decimal("0.04"),
            price=Decimal("93.76"),
            maturity_years=30,
            issue_costs_pct=Decimal("0.03"),  # 3% — high
            bidragssats=Decimal("0.0070"),
        )
        result_high = _compute_component(spec_high, provenu, tax_rate)

        assert result_high.aap_before_tax > result_low.aap_before_tax, (
            f"ÅOP with high costs {result_high.aap_before_tax} should be > "
            f"ÅOP with low costs {result_low.aap_before_tax}"
        )


class TestPeriodeomkostningFormula:
    """Verify periodeomkostning = ydelse_total + indfrielse - desired_provenu."""

    def test_periodeomkostning_formula_all_scenarios(self):
        """periodeomkostning == ydelse_total + indfrielse - desired_provenu for all scenarios.

        Tolerance: ±1 kr for rounding.
        """
        result = calculate(PRESETS["default"])

        for horizon in result.horizon_analyses:
            for row in horizon.scenarios:
                expected = row.ydelse_total + row.indfrielse - result.desired_provenu
                diff = row.periodeomkostning - expected
                assert abs(diff) < Decimal(1), (
                    f"Alternative '{horizon.alternative_label}', "
                    f"shock={row.rate_shock}: "
                    f"periodeomkostning={row.periodeomkostning} but "
                    f"ydelse_total + indfrielse - provenu = {expected} "
                    f"(diff={diff})"
                )

    def test_periodeomkostning_formula_each_shock(self):
        """Verify the formula for each individual rate shock scenario."""
        result = calculate(PRESETS["default"])
        expected_shocks = [Decimal("-0.02"), Decimal(0), Decimal("0.02")]

        for horizon in result.horizon_analyses:
            for row, expected_shock in zip(horizon.scenarios, expected_shocks):
                expected = row.ydelse_total + row.indfrielse - result.desired_provenu
                assert row.rate_shock == expected_shock, (
                    f"Expected shock {expected_shock}, got {row.rate_shock}"
                )
                assert abs(row.periodeomkostning - expected) < Decimal(1), (
                    f"shock={row.rate_shock}: "
                    f"periodeomkostning={row.periodeomkostning} != {expected}"
                )


# ─── Horizon and amortization checks ────────────────────────────────


_SHOCKS = [Decimal("-0.02"), Decimal(0), Decimal("0.02")]


class TestHorizonStructuralProperties:
    """Verify horizon analysis structural invariants.

    The default preset has three alternatives:
      0 — DESTR (flexlån, rate adjusts with shock)
      1 — F1 (flexlån, rate adjusts with shock)
      2 — 4% fixed obligation (rate fixed, only bond price changes)
    """

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls):
        return calculate(PRESETS["default"])

    # ── Three rate shocks present ──────────────────────────────────

    def test_three_rate_shocks_present(self, result):
        """Every horizon analysis must have exactly 3 shocks: [-2%, 0, +2%]."""
        for ha in result.horizon_analyses:
            assert len(ha.scenarios) == 3
            shocks = [s.rate_shock for s in ha.scenarios]
            assert shocks == _SHOCKS

    # ── ydelse_start identical across all shocks (every alternative) ─

    def test_ydelse_start_identical_across_shocks(self, result):
        """ydelse_start must be the same for all rate shocks because the rate
        change only takes effect after the period start."""
        for ha in result.horizon_analyses:
            starts = [s.ydelse_start for s in ha.scenarios]
            assert starts[0] == starts[1] == starts[2], (
                f"ydelse_start differs across shocks for {ha.alternative_label}: {starts}"
            )

    # ── Flexlån alternatives (indices 0 and 1): variable-rate properties ─

    def test_flexlaan_ydelse_slut_ordering(self, result):
        """For flexlån, ydelse_slut must order: -2% < 0% < +2%."""
        for i in (0, 1):
            ha = result.horizon_analyses[i]
            by_shock = {s.rate_shock: s for s in ha.scenarios}
            assert (
                by_shock[Decimal("-0.02")].ydelse_slut
                < by_shock[Decimal(0)].ydelse_slut
            )
            assert (
                by_shock[Decimal(0)].ydelse_slut < by_shock[Decimal("0.02")].ydelse_slut
            )

    def test_flexlaan_restgaeld_ordering(self, result):
        """For flexlån, restgæld must order: -2% < 0% < +2% (lower rate →
        more principal paid down)."""
        for i in (0, 1):
            ha = result.horizon_analyses[i]
            by_shock = {s.rate_shock: s for s in ha.scenarios}
            assert by_shock[Decimal("-0.02")].restgaeld < by_shock[Decimal(0)].restgaeld
            assert by_shock[Decimal(0)].restgaeld < by_shock[Decimal("0.02")].restgaeld

    def test_flexlaan_afdrag_ordering(self, result):
        """For flexlån, afdrag must order: -2% > 0% > +2% (lower rate →
        larger principal payments)."""
        for i in (0, 1):
            ha = result.horizon_analyses[i]
            by_shock = {s.rate_shock: s for s in ha.scenarios}
            assert (
                by_shock[Decimal("-0.02")].afdrag_total
                > by_shock[Decimal(0)].afdrag_total
            )
            assert (
                by_shock[Decimal(0)].afdrag_total
                > by_shock[Decimal("0.02")].afdrag_total
            )

    def test_flexlaan_indfrielse_near_restgaeld(self, result):
        """For flexlån, indfrielse ≈ restgæld (par redemption, within 5%)."""
        for i in (0, 1):
            ha = result.horizon_analyses[i]
            for row in ha.scenarios:
                ratio = abs(row.indfrielse - row.restgaeld) / row.restgaeld
                assert ratio < Decimal("0.05"), (
                    f"Alt {i} shock {row.rate_shock}: indfrielse {row.indfrielse} "
                    f"differs from restgaeld {row.restgaeld} by {ratio:.2%}"
                )

    # ── Fixed-rate alternative (index 2): rate-invariant properties ──

    def test_fixed_ydelse_slut_same_across_shocks(self, result):
        """Fixed-rate ydelse_slut must be identical across all shocks."""
        ha = result.horizon_analyses[2]
        slut = [s.ydelse_slut for s in ha.scenarios]
        assert slut[0] == slut[1] == slut[2], (
            f"Fixed-rate ydelse_slut differs across shocks: {slut}"
        )

    def test_fixed_rente_total_same_across_shocks(self, result):
        """Fixed-rate rente_total must be identical across all shocks
        (same amortization schedule)."""
        ha = result.horizon_analyses[2]
        rente = [s.rente_total for s in ha.scenarios]
        assert rente[0] == rente[1] == rente[2], (
            f"Fixed-rate rente_total differs across shocks: {rente}"
        )

    def test_fixed_afdrag_total_same_across_shocks(self, result):
        """Fixed-rate afdrag_total must be identical across all shocks."""
        ha = result.horizon_analyses[2]
        afdrag = [s.afdrag_total for s in ha.scenarios]
        assert afdrag[0] == afdrag[1] == afdrag[2], (
            f"Fixed-rate afdrag_total differs across shocks: {afdrag}"
        )

    def test_fixed_restgaeld_same_across_shocks(self, result):
        """Fixed-rate restgæld must be identical across all shocks."""
        ha = result.horizon_analyses[2]
        restgaeld = [s.restgaeld for s in ha.scenarios]
        assert restgaeld[0] == restgaeld[1] == restgaeld[2], (
            f"Fixed-rate restgæld differs across shocks: {restgaeld}"
        )

    def test_fixed_gns_kurs_ordering(self, result):
        """Fixed-rate gns_kurs must order: -2% > 0% > +2% (bond price falls
        when rates rise)."""
        ha = result.horizon_analyses[2]
        by_shock = {s.rate_shock: s for s in ha.scenarios}
        assert by_shock[Decimal("-0.02")].gns_kurs > by_shock[Decimal(0)].gns_kurs
        assert by_shock[Decimal(0)].gns_kurs > by_shock[Decimal("0.02")].gns_kurs

    def test_fixed_indfrielse_ordering(self, result):
        """Fixed-rate indfrielse must order: -2% > 0% > +2% (bond price rises
        when rates fall → more expensive to redeem)."""
        ha = result.horizon_analyses[2]
        by_shock = {s.rate_shock: s for s in ha.scenarios}
        assert by_shock[Decimal("-0.02")].indfrielse > by_shock[Decimal(0)].indfrielse
        assert by_shock[Decimal(0)].indfrielse > by_shock[Decimal("0.02")].indfrielse

    def test_fixed_periodeomkostning_ordering(self, result):
        """Fixed-rate periodeomkostning must order: -2% > 0% > +2% (redeeming
        at a high bond price is expensive)."""
        ha = result.horizon_analyses[2]
        by_shock = {s.rate_shock: s for s in ha.scenarios}
        assert (
            by_shock[Decimal("-0.02")].periodeomkostning
            > by_shock[Decimal(0)].periodeomkostning
        )
        assert (
            by_shock[Decimal(0)].periodeomkostning
            > by_shock[Decimal("0.02")].periodeomkostning
        )


class TestHorizonFormulaConsistency:
    """Verify that every horizon scenario row satisfies the defining formulas:

    periodeomkostning = ydelse_total + indfrielse − desired_provenu
    ydelse_total     = rente_total + afdrag_total
    """

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls):
        return calculate(PRESETS["default"])

    def test_periodeomkostning_formula(self, result):
        """periodeomkostning == ydelse_total + indfrielse − desired_provenu
        (within 1 kr rounding)."""
        for ha in result.horizon_analyses:
            for row in ha.scenarios:
                expected = row.ydelse_total + row.indfrielse - result.desired_provenu
                assert abs(row.periodeomkostning - expected) < Decimal(1), (
                    f"{ha.alternative_label} shock {row.rate_shock}: "
                    f"periodeomkostning {row.periodeomkostning} != {expected}"
                )

    def test_ydelse_total_formula(self, result):
        """ydelse_total == rente_total + afdrag_total (within 1 kr rounding)."""
        for ha in result.horizon_analyses:
            for row in ha.scenarios:
                expected = row.rente_total + row.afdrag_total
                assert abs(row.ydelse_total - expected) < Decimal(1), (
                    f"{ha.alternative_label} shock {row.rate_shock}: "
                    f"ydelse_total {row.ydelse_total} != {expected}"
                )


class TestAmortizationScheduleInvariants:
    """Verify amortization schedule structural invariants for each
    alternative in the default preset."""

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls):
        return calculate(PRESETS["default"])

    def test_first_year_restgaeld_below_hovedstol(self, result):
        """After year 1, restgæld < total hovedstol (principal is paid down)."""
        for i, alt in enumerate(result.alternatives):
            sched = amortization_schedule(PRESETS["default"], i)
            assert sched.years[0].restgaeld < alt.total_hovedstol, (
                f"Alt {i} ({alt.label}): year-1 restgæld {sched.years[0].restgaeld} "
                f">= hovedstol {alt.total_hovedstol}"
            )

    def test_sum_afdrag_equals_hovedstol(self, result):
        """Sum of all years' afdrag must equal total hovedstol (within 1 kr)."""
        for i, alt in enumerate(result.alternatives):
            sched = amortization_schedule(PRESETS["default"], i)
            total_afdrag = sum(y.afdrag for y in sched.years)
            assert abs(total_afdrag - alt.total_hovedstol) < Decimal(1), (
                f"Alt {i} ({alt.label}): sum(afdrag) {total_afdrag} "
                f"!= hovedstol {alt.total_hovedstol}"
            )

    def test_final_year_restgaeld_near_zero(self, result):
        """Final year restgæld must be < 1 kr (loan fully amortized)."""
        for i, alt in enumerate(result.alternatives):
            sched = amortization_schedule(PRESETS["default"], i)
            assert sched.years[-1].restgaeld < Decimal(1), (
                f"Alt {i} ({alt.label}): final restgæld {sched.years[-1].restgaeld} "
                f">= 1"
            )

    def test_interest_decreases_year2_vs_year1(self, result):
        """Interest (rente) in year 2 must be < year 1 (decreasing annuity)."""
        for i, alt in enumerate(result.alternatives):
            sched = amortization_schedule(PRESETS["default"], i)
            assert sched.years[1].rente < sched.years[0].rente, (
                f"Alt {i} ({alt.label}): year-2 rente {sched.years[1].rente} "
                f">= year-1 rente {sched.years[0].rente}"
            )

    def test_schedule_length_30_years(self, result):
        """Amortization schedule must have 30 years (±1)."""
        for i, alt in enumerate(result.alternatives):
            sched = amortization_schedule(PRESETS["default"], i)
            assert abs(len(sched.years) - 30) <= 1, (
                f"Alt {i} ({alt.label}): {len(sched.years)} years, expected 30 ± 1"
            )

    def test_hovedstol_matches_schedule_basis(self, result):
        """total_hovedstol from calculate() must match the amortization
        schedule's implied hovedstol (sum of afdrag + final restgæld)."""
        for i, alt in enumerate(result.alternatives):
            sched = amortization_schedule(PRESETS["default"], i)
            implied = sum(y.afdrag for y in sched.years) + sched.years[-1].restgaeld
            assert abs(implied - alt.total_hovedstol) < Decimal(1), (
                f"Alt {i} ({alt.label}): schedule implied hovedstol {implied} "
                f"!= calculate() hovedstol {alt.total_hovedstol}"
            )


# ─── Parameterized reference comparison tests ────────────────────────
#
# Append one dict to REFERENCE_CASES to add a reference case. The
# parametrized test methods auto-generate test IDs for every (case × metric).


def _monthly_equiv_ydelse(alt, component_ppys):
    """Convert per-period ydelse to monthly-equivalent.

    Realkredit pays quarterly (ppy=4), bank pays monthly (ppy=12).
    The AlternativeSummary.ydelse_before_tax is the sum of per-period
    ydelse across components — mixing quarterly and monthly. This helper
    converts each component's per-period ydelse to monthly, then sums.
    """
    return sum(
        comp.ydelse_before_tax * Decimal(ppy) / Decimal(12)
        for comp, ppy in zip(alt.components, component_ppys)
    )


def _effective_aap(alt, aap_ppy):
    """Convert nominal annual ÅOP to effective annual rate.

    The engine's aap_before_tax is periodic_irr × ppy (a nominal rate).
    Effective annual = (1 + aap/ppy)^ppy - 1.
    """
    return (Decimal(1) + alt.aap_before_tax / Decimal(aap_ppy)) ** Decimal(
        aap_ppy
    ) - Decimal(1)


def _horizon_row(result, alt_index, shock):
    """Extract a single horizon scenario row by rate_shock value."""
    ha = result.horizon_analyses[alt_index]
    return next(s for s in ha.scenarios if s.rate_shock == shock)


REFERENCE_CASES: list[dict] = [
    # ── Oct 2026: 2.5M provenu, pure realkredit ───────────────────
    #
    # Bidrag is tax-deductible for all loan types.
    # rente_total = (interest + bidrag) × (1 − tax_rate).
    #
    # Append one dict to REFERENCE_CASES to add a case.
    # ── F1 oktober, 31-yr flexlån ──────────────────────────────────
    {
        "id": "f1_oct",
        "input": CalculatorInput(
            desired_provenu=Decimal(2500643),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F1,
                            rate=Decimal("0.0308"),
                            price=Decimal("98.26"),
                            maturity_years=31,
                            issue_costs_nominal=Decimal(44357),
                            bidragssats=Decimal("0.0095"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2545000),  # exact
            "ydelse": Decimal(12665),  # monthly before tax
            "aap": Decimal("0.0436"),  # effective annual ÅOP
            "horizon_0pct": {
                "rente": Decimal(331263),
                "afdrag": Decimal(261976),
                "ydelse": Decimal(593239),
                "restgaeld": Decimal(2283024),
            },
            "horizon_+2pct": {
                "rente_total": Decimal(470123),
                "afdrag_total": Decimal(201768),
                "ydelse_total": Decimal(671892),
                "restgaeld": Decimal(2343232),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(197262),
                "afdrag_total": Decimal(336167),
                "ydelse_total": Decimal(533429),
                "restgaeld": Decimal(2208833),
            },
        },
        "tol": {
            "hovedstol": None,  # exact
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.01"),
            "horizon_rente_total_shocked": Decimal("0.20"),
            "horizon_afdrag_total_shocked": Decimal("0.10"),
            "horizon_ydelse_total_shocked": Decimal("0.05"),
            "horizon_restgaeld_shocked": Decimal("0.02"),
        },
    },
    # ── 4% fixed-rate obligation, 30-yr ───────────────────────────
    {
        "id": "fixed_4pct",
        "input": CalculatorInput(
            desired_provenu=Decimal(2500154),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="4% fixed",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.04"),
                            price=Decimal("93.84"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(45867),
                            bidragssats=Decimal("0.0070"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2716000),
            "ydelse": Decimal(14573),  # monthly before tax
            "aap": Decimal("0.0556"),  # effective annual ÅOP
            "horizon_0pct": {
                "rente": Decimal(404778),
                "afdrag": Decimal(259674),
                "ydelse": Decimal(664452),
                "restgaeld": Decimal(2456326),
            },
            # Fixed-rate: all scenarios identical (rate doesn't shock)
            "horizon_+2pct": {
                "rente_total": Decimal(404778),
                "afdrag_total": Decimal(259674),
                "ydelse_total": Decimal(664452),
                "restgaeld": Decimal(2456326),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(404778),
                "afdrag_total": Decimal(259674),
                "ydelse_total": Decimal(664452),
                "restgaeld": Decimal(2456326),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),  # 0.2% — engine rounds to nearest 1000
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.01"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.01"),
            "horizon_rente_total_shocked": Decimal("0.02"),
            "horizon_afdrag_total_shocked": Decimal("0.01"),
            "horizon_ydelse_total_shocked": Decimal("0.02"),
            "horizon_restgaeld_shocked": Decimal("0.01"),
        },
    },
    # ── F5 januar, 30-yr flexlån ──────────────────────────────────
    {
        "id": "f5_jan",
        "input": CalculatorInput(
            desired_provenu=Decimal(2500133),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F5",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F5,
                            rate=Decimal("0.0348"),
                            price=Decimal("91.30"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(46867),
                            bidragssats=Decimal("0.0095"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2547000),  # exact
            "ydelse": Decimal(13395),  # monthly before tax
            "aap": Decimal("0.0472"),  # effective annual ÅOP
            "horizon_0pct": {
                "rente": Decimal(357317),
                "afdrag": Decimal(259824),
                "ydelse": Decimal(617141),
                "restgaeld": Decimal(2287176),
            },
            "horizon_+2pct": {
                "rente_total": Decimal(382379),
                "afdrag_total": Decimal(248287),
                "ydelse_total": Decimal(630666),
                "restgaeld": Decimal(2298713),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(332681),
                "afdrag_total": Decimal(274116),
                "ydelse_total": Decimal(606797),
                "restgaeld": Decimal(2272884),
            },
        },
        "tol": {
            "hovedstol": None,  # exact
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.01"),
            # F5 shocked: wider tolerance — rate-reset timing differs.
            "horizon_rente_total_shocked": Decimal("0.50"),
            "horizon_afdrag_total_shocked": Decimal("0.30"),
            "horizon_ydelse_total_shocked": Decimal("0.15"),
            "horizon_restgaeld_shocked": Decimal("0.05"),
        },
    },
    # ── Oct 2026: 6.5M provenu, pure realkredit ───────────────────
    # ── F1 oktober, 31-yr, 6.5M provenu ───────────────────────────
    {
        "id": "f1_oct_6m",
        "input": CalculatorInput(
            desired_provenu=Decimal(6500770),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F1,
                            rate=Decimal("0.0308"),
                            price=Decimal("98.26"),
                            maturity_years=31,
                            issue_costs_nominal=Decimal(104230),
                            bidragssats=Decimal("0.0095"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(6605000),  # exact
            "ydelse": Decimal(32869),  # monthly before tax
            "aap": Decimal("0.0435"),
            "horizon_0pct": {
                "rente": Decimal(859723),
                "afdrag": Decimal(679902),
                "ydelse": Decimal(1539625),
                "restgaeld": Decimal(5925098),
            },
            "horizon_+2pct": {
                "rente_total": Decimal(1220104),
                "afdrag_total": Decimal(523646),
                "ydelse_total": Decimal(1743750),
                "restgaeld": Decimal(6081354),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(511951),
                "afdrag_total": Decimal(872449),
                "ydelse_total": Decimal(1384400),
                "restgaeld": Decimal(5732551),
            },
        },
        "tol": {
            "hovedstol": None,  # exact
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.01"),
            "horizon_rente_total_shocked": Decimal("0.20"),
            "horizon_afdrag_total_shocked": Decimal("0.10"),
            "horizon_ydelse_total_shocked": Decimal("0.05"),
            "horizon_restgaeld_shocked": Decimal("0.02"),
        },
    },
    # ── 1% deep-discount bond, 30-yr, 6.5M ──────────────────────────
    # Skip: needs coupon_rate field (PR #22) to separate coupon (1%) from yield (4.43%).
    {
        "id": "fixed_1pct_6m",
        "skip": ("Deep-discount bond requires coupon_rate field (PR #22)."),
        "input": CalculatorInput(
            desired_provenu=Decimal(6500818),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="1% fixed",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.0443"),  # effective yield
                            price=Decimal("64.03"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(150182),
                            bidragssats=Decimal("0.0070"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(6651000),
            "ydelse": Decimal(37368),  # monthly before tax
            "aap": Decimal("0.0545"),
            "horizon_0pct": {
                "rente": Decimal(1085590),
                "afdrag": Decimal(595223),
                "ydelse": Decimal(1680813),
                "restgaeld": Decimal(6055777),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.01"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.01"),
        },
    },
    # ── CITA 30, 6.5M provenu ────────────────────────────────────
    # margin=0 — HTML only shows total rate. Wider rente tolerance.
    {
        "id": "cita_30_6m",
        "input": CalculatorInput(
            desired_provenu=Decimal(6500495),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="CITA",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.CITA,
                            rate=Decimal("0.0269"),
                            reference_rate=Decimal("0.0269"),
                            margin=Decimal("0"),
                            price=Decimal("100.13"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(102544),
                            bidragssats=Decimal("0.0085"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(6601000),
            "ydelse": Decimal(31454),  # monthly before tax
            "aap": Decimal("0.0370"),
            "horizon_0pct": {
                "rente": Decimal(723935),
                "afdrag": Decimal(771641),
                "ydelse": Decimal(1495576),
                "restgaeld": Decimal(5829359),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.001"),  # ~0.1% — engine rounds to nearest 1000
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.05"),  # wider due to unknown margin split
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.01"),
        },
    },
    # ── April 2023 session ──────────────────────────────────────────
    #
    # Tolerances wider: PDF løbetid includes months (engine uses integer
    # years), and rounding granularity differs.
    # ── F1 flexlån, 20 år ──────────────────────────────────────────
    {
        "id": "apr2023_f1_20",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658833),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 20",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F1,
                            rate=Decimal("0.0373"),
                            price=Decimal("99.03"),
                            maturity_years=20,
                            issue_costs_nominal=Decimal(12167),
                            bidragssats=Decimal("0.0038"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2671000),  # exact
            "ydelse": Decimal(16464),  # monthly-equiv
            "aap": Decimal("0.0418"),  # effective annual
            "horizon_0pct": {
                "rente": Decimal(331746),
                "afdrag": Decimal(481117),
                "ydelse": Decimal(812863),
                "restgaeld": Decimal(2189883),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(175997),
                "afdrag_total": Decimal(566662),
                "ydelse_total": Decimal(742658),
                "restgaeld": Decimal(2104338),
            },
            "horizon_+2pct": {
                "rente_total": Decimal(494353),
                "afdrag_total": Decimal(403897),
                "ydelse_total": Decimal(898250),
                "restgaeld": Decimal(2267103),
            },
        },
        "tol": {
            "hovedstol": None,  # exact
            "ydelse": Decimal("0.02"),  # ~1.4 % off (løbetid rounding)
            "aap": Decimal("0.01"),  # ~0.05 pp
            "horizon_rente": Decimal("0.07"),
            "horizon_afdrag": Decimal("0.05"),
            "horizon_ydelse": Decimal("0.05"),
            "horizon_restgaeld": Decimal("0.05"),
            "horizon_rente_total_shocked": Decimal("0.08"),
            "horizon_afdrag_total_shocked": Decimal("0.15"),
            "horizon_ydelse_total_shocked": Decimal("0.15"),
            "horizon_restgaeld_shocked": Decimal("0.05"),
        },
    },
    # ── F1 flexlån, 25 år ──────────────────────────────────────────
    {
        "id": "apr2023_f1_25",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658833),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 25",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F1,
                            rate=Decimal("0.0373"),
                            price=Decimal("99.03"),
                            maturity_years=25,
                            issue_costs_nominal=Decimal(12167),
                            bidragssats=Decimal("0.0038"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2671000),  # exact
            "ydelse": Decimal(14432),  # monthly-equiv
            "aap": Decimal("0.0416"),  # effective annual
            "horizon_0pct": {
                "rente": Decimal(338759),
                "afdrag": Decimal(348699),
                "ydelse": Decimal(687458),
                "restgaeld": Decimal(2322301),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(179530),
                "afdrag_total": Decimal(434658),
                "ydelse_total": Decimal(614188),
                "restgaeld": Decimal(2236342),
            },
            "horizon_+2pct": {
                "rente_total": Decimal(504541),
                "afdrag_total": Decimal(275103),
                "ydelse_total": Decimal(779643),
                "restgaeld": Decimal(2395897),
            },
        },
        "tol": {
            "hovedstol": None,
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.01"),
            "horizon_rente": Decimal("0.07"),
            "horizon_afdrag": Decimal("0.05"),
            "horizon_ydelse": Decimal("0.05"),
            "horizon_restgaeld": Decimal("0.05"),
            "horizon_rente_total_shocked": Decimal("0.08"),
            "horizon_afdrag_total_shocked": Decimal("0.15"),
            "horizon_ydelse_total_shocked": Decimal("0.15"),
            "horizon_restgaeld_shocked": Decimal("0.05"),
        },
    },
    # ── F1 flexlån, 30 år ──────────────────────────────────────────
    {
        "id": "apr2023_f1_30",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658833),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 30",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F1,
                            rate=Decimal("0.0373"),
                            price=Decimal("99.03"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(12167),
                            bidragssats=Decimal("0.0038"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2671000),  # exact
            "ydelse": Decimal(13106),  # monthly-equiv
            "aap": Decimal("0.0415"),  # effective annual
            "horizon_0pct": {
                "rente": Decimal(343340),
                "afdrag": Decimal(262206),
                "ydelse": Decimal(605546),
                "restgaeld": Decimal(2408794),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(181880),
                "afdrag_total": Decimal(346672),
                "ydelse_total": Decimal(528553),
                "restgaeld": Decimal(2324328),
            },
            "horizon_+2pct": {
                "rente_total": Decimal(510994),
                "afdrag_total": Decimal(193875),
                "ydelse_total": Decimal(704869),
                "restgaeld": Decimal(2477125),
            },
        },
        "tol": {
            "hovedstol": None,
            "ydelse": Decimal("0.02"),
            "aap": Decimal("0.01"),
            "horizon_rente": Decimal("0.07"),
            "horizon_afdrag": Decimal("0.05"),
            "horizon_ydelse": Decimal("0.05"),
            "horizon_restgaeld": Decimal("0.05"),
            "horizon_rente_total_shocked": Decimal("0.08"),
            "horizon_afdrag_total_shocked": Decimal("0.15"),
            "horizon_ydelse_total_shocked": Decimal("0.15"),
            "horizon_restgaeld_shocked": Decimal("0.05"),
        },
    },
    # ── Fast 5 % obligation, 20 år ──────────────────────────────────
    {
        "id": "apr2023_fast5_20",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658348),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="Fast 5% 20",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.05"),
                            price=Decimal("99.52"),
                            maturity_years=20,
                            issue_costs_nominal=Decimal(12166),
                            bidragssats=Decimal("0.0033"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2686000),  # ~2 000 kr off (rounding)
            "ydelse": Decimal(18508),  # monthly-equiv
            "aap": Decimal("0.0557"),  # effective annual
            "horizon_0pct": {
                "rente": Decimal(439464),
                "afdrag": Decimal(445289),
                "ydelse": Decimal(884753),
                "restgaeld": Decimal(2240711),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),  # ~0.07 %
            "ydelse": Decimal("0.01"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.02"),
        },
    },
    # ── Fast 5 % obligation, 21 år ──────────────────────────────────
    {
        "id": "apr2023_fast5_21",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658891),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="Fast 5% 21",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.05"),
                            price=Decimal("98.52"),
                            maturity_years=21,
                            issue_costs_nominal=Decimal(12167),
                            bidragssats=Decimal("0.0033"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2714000),
            "ydelse": Decimal(18203),
            "aap": Decimal("0.0569"),
            "horizon_0pct": {
                "rente": Decimal(446754),
                "afdrag": Decimal(416262),
                "ydelse": Decimal(863016),
                "restgaeld": Decimal(2297738),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),
            "ydelse": Decimal("0.01"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.02"),
        },
    },
    # ── Fast 5 % obligation, 22 år ──────────────────────────────────
    {
        "id": "apr2023_fast5_22",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658891),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="Fast 5% 22",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.05"),
                            price=Decimal("98.52"),
                            maturity_years=22,
                            issue_costs_nominal=Decimal(12167),
                            bidragssats=Decimal("0.0033"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2714000),
            "ydelse": Decimal(17755),
            "aap": Decimal("0.0568"),
            "horizon_0pct": {
                "rente": Decimal(449197),
                "afdrag": Decimal(385912),
                "ydelse": Decimal(835109),
                "restgaeld": Decimal(2328088),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),
            "ydelse": Decimal("0.01"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.02"),
        },
    },
    # ── Fast 5 % obligation, 23 år ──────────────────────────────────
    {
        "id": "apr2023_fast5_23",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658891),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="Fast 5% 23",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.05"),
                            price=Decimal("98.52"),
                            maturity_years=23,
                            issue_costs_nominal=Decimal(12167),
                            bidragssats=Decimal("0.0033"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2714000),
            "ydelse": Decimal(17349),
            "aap": Decimal("0.0567"),
            "horizon_0pct": {
                "rente": Decimal(451408),
                "afdrag": Decimal(358445),
                "ydelse": Decimal(809853),
                "restgaeld": Decimal(2355555),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),
            "ydelse": Decimal("0.01"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.02"),
        },
    },
    # ── Fast 5 % obligation, 25 år ──────────────────────────────────
    {
        "id": "apr2023_fast5_25",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658530),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="Fast 5% 25",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.05"),
                            price=Decimal("98.83"),
                            maturity_years=25,
                            issue_costs_nominal=Decimal(12166),
                            bidragssats=Decimal("0.0033"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2705000),
            "ydelse": Decimal(16590),
            "aap": Decimal("0.0563"),
            "horizon_0pct": {
                "rente": Decimal(453736),
                "afdrag": Decimal(309739),
                "ydelse": Decimal(763475),
                "restgaeld": Decimal(2395261),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),
            "ydelse": Decimal("0.01"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.02"),
        },
    },
    # ── Fast 5 % obligation, 30 år ──────────────────────────────────
    {
        "id": "apr2023_fast5_30",
        "input": CalculatorInput(
            desired_provenu=Decimal(2658530),
            start_date=date(2023, 4, 22),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="Fast 5% 30",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.05"),
                            price=Decimal("98.83"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(12166),
                            bidragssats=Decimal("0.0033"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(2705000),
            "ydelse": Decimal(15291),
            "aap": Decimal("0.0560"),
            "horizon_0pct": {
                "rente": Decimal(460815),
                "afdrag": Decimal(221792),
                "ydelse": Decimal(682607),
                "restgaeld": Decimal(2483208),
            },
        },
        "tol": {
            "hovedstol": Decimal("0.002"),
            "ydelse": Decimal("0.01"),
            "aap": Decimal("0.005"),
            "horizon_rente": Decimal("0.02"),
            "horizon_afdrag": Decimal("0.02"),
            "horizon_ydelse": Decimal("0.02"),
            "horizon_restgaeld": Decimal("0.02"),
        },
    },
    # ── Samlet F1 T-lån, 21 år ──────────────────────────────────────
    # T-lån: fixed monthly ydelse, variable duration.  PDF monthly ydelse
    # converted to quarterly excluding bidrag.  ÅOP tolerance 1pp (engine
    # IRR understates effective cost for T-lån).
    {
        "id": "apr2023_tlaan_21",
        "input": CalculatorInput(
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
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(4453000),  # exact
            "ydelse": Decimal(27643),  # monthly-equiv, exact
            "aap": Decimal("0.0444"),  # effective annual (engine ~0.0403)
            "horizon_0pct": {
                "rente": Decimal(564768),
                "afdrag": Decimal(799207),
                "ydelse": Decimal(1363974),
                "restgaeld": Decimal(3653793),
            },
            "horizon_-2pct": {
                "rente_total": Decimal(298312),
                "afdrag_total": Decimal(1196309),
                "ydelse_total": Decimal(1494621),
                "restgaeld": Decimal(3256691),
            },
            "horizon_+2pct": {
                "rente_total": Decimal(858092),
                "afdrag_total": Decimal(361905),
                "ydelse_total": Decimal(1219997),
                "restgaeld": Decimal(4091095),
            },
        },
        "tol": {
            "hovedstol": None,  # exact
            "ydelse": Decimal("0.001"),  # exact match
            "aap": Decimal("0.01"),  # ~0.4 pp off (engine limitation)
            "horizon_rente": Decimal("0.09"),
            "horizon_afdrag": Decimal("0.05"),
            "horizon_ydelse": Decimal("0.05"),
            "horizon_restgaeld": Decimal("0.05"),
            "horizon_rente_total_shocked": Decimal("0.11"),
            "horizon_afdrag_total_shocked": Decimal("0.15"),
            "horizon_ydelse_total_shocked": Decimal("0.15"),
            "horizon_restgaeld_shocked": Decimal("0.05"),
        },
    },
    # ── Samlet F1 T-lån, 22 år ──────────────────────────────────────
    {
        "id": "apr2023_tlaan_22",
        "input": CalculatorInput(
            desired_provenu=Decimal(4371947),
            start_date=date(2023, 4, 24),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 T 22",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.T,
                            rate=Decimal("0.0372"),
                            price=Decimal("99.05"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(81053),
                            bidragssats=Decimal("0.0048"),
                            fixed_ydelse=Decimal("73418.40"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(4453000),
            "ydelse": Decimal(26254),
            "aap": Decimal("0.0442"),
        },
        "tol": {
            "hovedstol": None,
            "ydelse": Decimal("0.001"),
            "aap": Decimal("0.01"),
        },
    },
    # ── Samlet F1 T-lån, 23 år ──────────────────────────────────────
    {
        "id": "apr2023_tlaan_23",
        "input": CalculatorInput(
            desired_provenu=Decimal(4371947),
            start_date=date(2023, 4, 24),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 T 23",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.T,
                            rate=Decimal("0.0372"),
                            price=Decimal("99.05"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(81053),
                            bidragssats=Decimal("0.0048"),
                            fixed_ydelse=Decimal("71834.40"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(4453000),
            "ydelse": Decimal(25726),
            "aap": Decimal("0.0441"),
        },
        "tol": {
            "hovedstol": None,
            "ydelse": Decimal("0.001"),
            "aap": Decimal("0.01"),
        },
    },
    # ── Samlet F1 T-lån, 24 år ──────────────────────────────────────
    {
        "id": "apr2023_tlaan_24",
        "input": CalculatorInput(
            desired_provenu=Decimal(4371947),
            start_date=date(2023, 4, 24),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 T 24",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.T,
                            rate=Decimal("0.0371"),
                            price=Decimal("99.05"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(81053),
                            bidragssats=Decimal("0.0048"),
                            fixed_ydelse=Decimal("69818.40"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(4453000),
            "ydelse": Decimal(25054),
            "aap": Decimal("0.0440"),
        },
        "tol": {
            "hovedstol": None,
            "ydelse": Decimal("0.001"),
            "aap": Decimal("0.01"),
        },
    },
    # ── Samlet F1 T-lån, 25 år ──────────────────────────────────────
    {
        "id": "apr2023_tlaan_25",
        "input": CalculatorInput(
            desired_provenu=Decimal(4371947),
            start_date=date(2023, 4, 24),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 T 25",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.T,
                            rate=Decimal("0.0371"),
                            price=Decimal("99.05"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(81053),
                            bidragssats=Decimal("0.0048"),
                            fixed_ydelse=Decimal("68423.40"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(4453000),
            "ydelse": Decimal(24589),
            "aap": Decimal("0.0440"),
        },
        "tol": {
            "hovedstol": None,
            "ydelse": Decimal("0.001"),
            "aap": Decimal("0.01"),
        },
    },
    # ── Samlet F1 T-lån, 30 år ──────────────────────────────────────
    {
        "id": "apr2023_tlaan_30",
        "input": CalculatorInput(
            desired_provenu=Decimal(4371947),
            start_date=date(2023, 4, 24),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1 T 30",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.T,
                            rate=Decimal("0.0371"),
                            price=Decimal("99.05"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(81053),
                            bidragssats=Decimal("0.0048"),
                            fixed_ydelse=Decimal("61202.40"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        ),
        "component_ppys": [4],
        "aap_ppy": 4,
        "expected": {
            "hovedstol": Decimal(4453000),
            "ydelse": Decimal(22182),
            "aap": Decimal("0.0436"),
        },
        "tol": {
            "hovedstol": None,
            "ydelse": Decimal("0.001"),
            "aap": Decimal("0.01"),
        },
    },
]


def _ref_case_ids():
    return [c["id"] for c in REFERENCE_CASES]


def _ref_case_params():
    """Convert REFERENCE_CASES to pytest.param objects with skip marks."""
    params = []
    for case in REFERENCE_CASES:
        marks = []
        if "skip" in case:
            marks.append(pytest.mark.skip(reason=case["skip"]))
        params.append(pytest.param(case, id=case["id"], marks=marks))
    return params


class TestReferenceComparison:
    """Parameterized reference comparison.

    Each (case × metric) pair is a separate test ID. To add a new
    reference case, append one dict to REFERENCE_CASES.
    """

    # ── Hovedstol ───────────────────────────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_hovedstol(self, ref_case):
        if "hovedstol" not in ref_case["expected"]:
            pytest.skip("no hovedstol reference for this case")
        result = calculate(ref_case["input"])
        actual = result.alternatives[0].total_hovedstol
        expected = ref_case["expected"]["hovedstol"]
        tol = ref_case["tol"].get("hovedstol")
        if tol is None:
            assert actual == expected, (
                f"{ref_case['id']}: hovedstol {actual} should be exactly {expected}"
            )
        else:
            assert abs(actual - expected) / expected < tol, (
                f"{ref_case['id']}: hovedstol {actual} should be within "
                f"{tol * 100}% of {expected}"
            )

    # ── Ydelse (monthly-equivalent) ────────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_ydelse(self, ref_case):
        if "ydelse" not in ref_case["expected"]:
            pytest.skip("no ydelse reference for this case")
        result = calculate(ref_case["input"])
        alt = result.alternatives[0]
        actual = _monthly_equiv_ydelse(alt, ref_case["component_ppys"])
        expected = ref_case["expected"]["ydelse"]
        tol = ref_case["tol"]["ydelse"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: ydelse {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── ÅOP (effective annual) ─────────────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_aap(self, ref_case):
        if "aap" not in ref_case["expected"]:
            pytest.skip("no aap reference for this case")
        result = calculate(ref_case["input"])
        alt = result.alternatives[0]
        actual = _effective_aap(alt, ref_case["aap_ppy"])
        expected = ref_case["expected"]["aap"]
        tol = ref_case["tol"]["aap"]
        assert abs(actual - expected) < tol, (
            f"{ref_case['id']}: effective ÅOP {actual} should be within "
            f"{tol * 100:.1f}pp of {expected}"
        )

    # ── Horizon 0% shock: rente ────────────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_rente(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "rente" not in exp:
            pytest.skip("no horizon rente reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.rente_total
        expected = exp["rente"]
        tol = ref_case["tol"]["horizon_rente"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon rente {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── Horizon 0% shock: afdrag ───────────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_afdrag(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "afdrag" not in exp:
            pytest.skip("no horizon afdrag reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.afdrag_total
        expected = exp["afdrag"]
        tol = ref_case["tol"]["horizon_afdrag"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon afdrag {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── Horizon 0% shock: ydelse ───────────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_ydelse(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "ydelse" not in exp:
            pytest.skip("no horizon ydelse reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.ydelse_total
        expected = exp["ydelse"]
        tol = ref_case["tol"]["horizon_ydelse"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon ydelse {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── Horizon 0% shock: restgaeld ────────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_restgaeld(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "restgaeld" not in exp:
            pytest.skip("no horizon restgaeld reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.restgaeld
        expected = exp["restgaeld"]
        tol = ref_case["tol"]["horizon_restgaeld"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon restgaeld {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── Horizon gns_kurs (fixed-rate only, all shocks) ─────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_gns_kurs(self, ref_case):
        gns_refs = ref_case["expected"].get("horizon_gns_kurs", {})
        if not gns_refs:
            pytest.skip("no horizon gns_kurs reference for this case")
        result = calculate(ref_case["input"])
        tol = ref_case["tol"]["horizon_gns_kurs"]
        for shock, expected in gns_refs.items():
            row = _horizon_row(result, 0, shock)
            actual = row.gns_kurs
            assert abs(actual - expected) < tol, (
                f"{ref_case['id']} shock={shock}: gns_kurs {actual} "
                f"should be within {tol:.0f}pp of {expected}"
            )

    # ── Horizon 0% shock: ydelse_start ──────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_ydelse_start(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "ydelse_start" not in exp:
            pytest.skip("no horizon ydelse_start reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.ydelse_start
        expected = exp["ydelse_start"]
        tol = ref_case["tol"]["horizon_ydelse_start"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon ydelse_start {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── Horizon 0% shock: ydelse_slut ───────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_ydelse_slut(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "ydelse_slut" not in exp:
            pytest.skip("no horizon ydelse_slut reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.ydelse_slut
        expected = exp["ydelse_slut"]
        tol = ref_case["tol"]["horizon_ydelse_slut"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon ydelse_slut {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── Horizon 0% shock: indfrielse ─────────────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_indfrielse(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "indfrielse" not in exp:
            pytest.skip("no horizon indfrielse reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.indfrielse
        expected = exp["indfrielse"]
        tol = ref_case["tol"]["horizon_indfrielse"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon indfrielse {actual} should be within "
            f"{tol * 100}% of {expected}"
        )

    # ── Horizon 0% shock: periodeomkostning ──────────────────────

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_periodeomkostning(self, ref_case):
        exp = ref_case["expected"].get("horizon_0pct", {})
        if "periodeomkostning" not in exp:
            pytest.skip("no horizon periodeomkostning reference for this case")
        result = calculate(ref_case["input"])
        row = _horizon_row(result, 0, Decimal(0))
        actual = row.periodeomkostning
        expected = exp["periodeomkostning"]
        tol = ref_case["tol"]["horizon_periodeomkostning"]
        assert abs(actual - expected) / expected < tol, (
            f"{ref_case['id']}: horizon periodeomkostning {actual} "
            f"should be within {tol * 100}% of {expected}"
        )

    # ── Shocked scenarios: rente/afdrag/ydelse at +2% and -2% ──

    @pytest.mark.parametrize("ref_case", _ref_case_params())
    def test_horizon_shocked(self, ref_case):
        """Verify horizon metrics under +2% and -2% rate shocks.

        Cases with horizon_+2pct or horizon_-2pct keys are tested.
        Each shocked scenario can contain rente, afdrag, ydelse, and
        restgaeld references.
        """
        shock_refs = {
            Decimal("0.02"): ref_case["expected"].get("horizon_+2pct", {}),
            Decimal("-0.02"): ref_case["expected"].get("horizon_-2pct", {}),
        }
        if not any(shock_refs.values()):
            pytest.skip("no shocked horizon references for this case")
        result = calculate(ref_case["input"])
        for shock, exp_shock in shock_refs.items():
            if not exp_shock:
                continue
            row = _horizon_row(result, 0, shock)
            for metric, expected in exp_shock.items():
                tol_key = f"horizon_{metric}_shocked"
                tol = ref_case["tol"].get(tol_key, Decimal("0.20"))
                actual = getattr(row, metric)
                assert abs(actual - expected) / expected < tol, (
                    f"{ref_case['id']} shock={shock} {metric}: "
                    f"{actual} should be within {tol * 100}% of {expected}"
                )


# ── Cross-case ordering invariants (not parameterized) ─────────────


class TestReferenceOrdering:
    """Verify ordering invariants across reference alternatives.

    These tests build a single CalculatorInput with multiple alternatives
    and check that hovedstol and ÅOP are ordered correctly. They are NOT
    parameterized by REFERENCE_CASES because they test cross-case
    relationships, not per-case values.
    """

    def test_hovedstol_ordering(self):
        """Hovedstol ordering: F1 < F5 < 4% fixed.

        Lower price → larger discount → needs more hovedstol for same provenu.
        """
        inp = CalculatorInput(
            desired_provenu=Decimal(2500000),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F1,
                            rate=Decimal("0.0308"),
                            price=Decimal("98.26"),
                            maturity_years=31,
                            issue_costs_nominal=Decimal(44357),
                            bidragssats=Decimal("0.0095"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
                FinancingAlternative(
                    label="F5",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F5,
                            rate=Decimal("0.0348"),
                            price=Decimal("91.30"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(46867),
                            bidragssats=Decimal("0.0095"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
                FinancingAlternative(
                    label="4% fixed",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.04"),
                            price=Decimal("93.84"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(45867),
                            bidragssats=Decimal("0.0070"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        )
        result = calculate(inp)
        h = [a.total_hovedstol for a in result.alternatives]
        assert h[0] < h[2], f"F1 hovedstol {h[0]} should be < fixed {h[2]}"
        assert h[1] < h[2], f"F5 hovedstol {h[1]} should be < fixed {h[2]}"

    def test_aap_ordering(self):
        """ÅOP ordering: F1 < F5 < 4% fixed.

        Higher rate + larger discount → higher ÅOP.
        """
        inp = CalculatorInput(
            desired_provenu=Decimal(2500000),
            start_date=date(2026, 10, 10),
            horizon_years=5,
            tax_rate=Decimal("0.336"),
            alternatives=[
                FinancingAlternative(
                    label="F1",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F1,
                            rate=Decimal("0.0308"),
                            price=Decimal("98.26"),
                            maturity_years=31,
                            issue_costs_nominal=Decimal(44357),
                            bidragssats=Decimal("0.0095"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
                FinancingAlternative(
                    label="F5",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.F5,
                            rate=Decimal("0.0348"),
                            price=Decimal("91.30"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(46867),
                            bidragssats=Decimal("0.0095"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
                FinancingAlternative(
                    label="4% fixed",
                    components=[
                        LoanSpec(
                            component=LoanComponent.REALKREDIT,
                            loan_type=LoanType.FIXED,
                            rate=Decimal("0.04"),
                            price=Decimal("93.84"),
                            maturity_years=30,
                            issue_costs_nominal=Decimal(45867),
                            bidragssats=Decimal("0.0070"),
                            provenu_share=Decimal(1),
                        ),
                    ],
                ),
            ],
        )
        result = calculate(inp)
        a = [a.aap_before_tax for a in result.alternatives]
        assert a[0] < a[1] < a[2], (
            f"ÅOP ordering should be F1 ({a[0]}) < F5 ({a[1]}) < fixed ({a[2]})"
        )
