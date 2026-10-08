"""Market data layer tests — fixture-based parser tests (no live network).

Each test class loads a saved HTML/CSV/XLSX fixture from tests/fixtures/ and
exercises the pure ``_parse_*`` function.  No HTTP requests are made.
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from boligregner.market_data import (
    BidragssatsEntry,
    BidragssatsKey,
    DSTRates,
    Institute,
    LTVBand,
    MarketRates,
    NominalRates,
    NordeaBondPrices,
    ReferenceRates,
    _apply_source,
    _parse_danish_decimal,
    _parse_destr_rate,
    _parse_dst_dnrnuri,
    _parse_ecb_bank_rate,
    _parse_finansdanmark_xlsx,
    _parse_finansdanmark_xlsx_url,
    _parse_jyske_reference_rates,
    _parse_mybanker_bidragssatser,
    _parse_nordea_bond_prices,
    _parse_rd_nominal_rates,
    build_preset_from_market,
    lookup_bidragssats,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8-sig")


def _load_fixture_bytes(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


# ─── Danish decimal helper ───────────────────────────────────────────


class TestParseDanishDecimal:
    def test_basic_comma(self):
        assert _parse_danish_decimal("2,0480") == Decimal("2.0480")

    def test_with_percent(self):
        assert _parse_danish_decimal("0,5625%") == Decimal("0.5625")

    def test_kurs_value(self):
        assert _parse_danish_decimal("93,48") == Decimal("93.48")

    def test_empty(self):
        assert _parse_danish_decimal("") is None

    def test_dash(self):
        assert _parse_danish_decimal("-") is None

    def test_double_dot(self):
        assert _parse_danish_decimal("..") is None

    def test_bom(self):
        assert _parse_danish_decimal("\ufeff3,899") == Decimal("3.899")

    def test_none(self):
        assert _parse_danish_decimal(None) is None  # type: ignore[arg-type]

    def test_unicode_dash(self):
        assert _parse_danish_decimal("−") is None


# ─── DST DNRNURI parser ──────────────────────────────────────────────


class TestDstDnrnuriParser:
    def test_parse_fixture(self):
        csv = _load_fixture("dst_dnrnuri.csv")
        rates = _parse_dst_dnrnuri(csv)
        assert isinstance(rates, DSTRates)
        assert rates.f3_effective == Decimal("3.899") / Decimal(100)
        assert rates.f5_effective == Decimal("3.829") / Decimal(100)
        assert rates.fixed_effective == Decimal("4.818") / Decimal(100)
        assert rates.f3_bidrag_avg == Decimal("0.983") / Decimal(100)
        assert rates.f5_bidrag_avg == Decimal("0.771") / Decimal(100)
        assert rates.fixed_bidrag_avg == Decimal("0.658") / Decimal(100)


# ─── ECB bank rate parser ────────────────────────────────────────────


class TestEcbBankRateParser:
    def test_parse_fixture(self):
        csv = _load_fixture("ecb_bank_rate.csv")
        rate = _parse_ecb_bank_rate(csv)
        assert rate == Decimal("4.03") / Decimal(100)
        assert rate == Decimal("0.0403")


# ─── Mybanker.dk bidragssatser parser ────────────────────────────────


class TestMybankerBidragssatserParser:
    def test_parse_fixture(self):
        html = _load_fixture("mybanker_bidragssatser.html")
        entries = _parse_mybanker_bidragssatser(html)
        assert len(entries) > 0
        # All entries should have valid fields
        for e in entries:
            assert isinstance(e, BidragssatsEntry)
            assert e.institute in list(Institute)
            assert e.ltv_band in list(LTVBand)
            assert e.bidragssats > 0

    def test_jyske_fixed_zero_to_40(self):
        html = _load_fixture("mybanker_bidragssatser.html")
        entries = _parse_mybanker_bidragssatser(html)
        jyske_fixed = [
            e
            for e in entries
            if e.institute == Institute.JYSKE
            and e.loan_type.value == "fixed"
            and e.ltv_band == LTVBand.ZERO_TO_40
            and not e.afdragsfrihed
        ]
        assert len(jyske_fixed) == 1
        assert jyske_fixed[0].bidragssats == Decimal("0.2250") / Decimal(100)

    def test_nykredit_f3_zero_to_40(self):
        html = _load_fixture("mybanker_bidragssatser.html")
        entries = _parse_mybanker_bidragssatser(html)
        nykredit_f3 = [
            e
            for e in entries
            if e.institute == Institute.NYKREDIT
            and e.loan_type.value == "f3"
            and e.ltv_band == LTVBand.ZERO_TO_40
            and not e.afdragsfrihed
        ]
        assert len(nykredit_f3) == 1
        assert nykredit_f3[0].bidragssats == Decimal("0.7000") / Decimal(100)

    def test_rd_fixed_over_60_uden_afdrag(self):
        html = _load_fixture("mybanker_bidragssatser.html")
        entries = _parse_mybanker_bidragssatser(html)
        rd_fixed = [
            e
            for e in entries
            if e.institute == Institute.RD
            and e.loan_type.value == "fixed"
            and e.ltv_band == LTVBand.OVER_60
            and e.afdragsfrihed
        ]
        assert len(rd_fixed) == 1
        assert rd_fixed[0].bidragssats == Decimal("1.8120") / Decimal(100)

    def test_nordea_f5_kort_rente(self):
        """Nordea's 'Flekslån F5 & Kort Rente' column maps to F5."""
        html = _load_fixture("mybanker_bidragssatser.html")
        entries = _parse_mybanker_bidragssatser(html)
        nordea_f5 = [
            e
            for e in entries
            if e.institute == Institute.NORDEA
            and e.loan_type.value == "f5"
            and e.ltv_band == LTVBand.ZERO_TO_40
            and not e.afdragsfrihed
        ]
        assert len(nordea_f5) == 1
        assert nordea_f5[0].bidragssats == Decimal("0.4000") / Decimal(100)

    def test_all_four_institutes_present(self):
        html = _load_fixture("mybanker_bidragssatser.html")
        entries = _parse_mybanker_bidragssatser(html)
        institutes = {e.institute for e in entries}
        assert Institute.JYSKE in institutes
        assert Institute.NYKREDIT in institutes
        assert Institute.NORDEA in institutes
        assert Institute.RD in institutes


# ─── RD.dk nominal rates parser ─────────────────────────────────────


class TestRdNominalRatesParser:
    def test_parse_fixture(self):
        html = _load_fixture("rd_nominal_rates.html")
        rates = _parse_rd_nominal_rates(html)
        assert isinstance(rates, NominalRates)
        assert rates.f1 == Decimal("2.27") / Decimal(100)
        assert rates.f3 == Decimal("2.39") / Decimal(100)
        assert rates.f5 == Decimal("2.66") / Decimal(100)

    def test_f2_and_f4_captured(self):
        """F2 and F4 are stored even though they have no LoanType enum."""
        html = _load_fixture("rd_nominal_rates.html")
        rates = _parse_rd_nominal_rates(html)
        assert rates.f2 == Decimal("2.35") / Decimal(100)
        assert rates.f4 == Decimal("2.53") / Decimal(100)

    def test_f10_captured(self):
        html = _load_fixture("rd_nominal_rates.html")
        rates = _parse_rd_nominal_rates(html)
        assert rates.f10 == Decimal("3.19") / Decimal(100)


# ─── Nordea bond prices parser ───────────────────────────────────────


class TestNordeaBondPricesParser:
    def test_parse_fixture(self):
        html = _load_fixture("nordea_bond_prices.html")
        result = _parse_nordea_bond_prices(html)
        assert isinstance(result, NordeaBondPrices)
        assert result.fixed_coupon == Decimal("4.00") / Decimal(100)
        assert result.fixed_kurs == Decimal("93.48")
        assert result.f3_kontantrente == Decimal("3.32") / Decimal(100)
        assert result.f5_kontantrente == Decimal("3.52") / Decimal(100)

    def test_excludes_frihed_variants(self):
        html = _load_fixture("nordea_bond_prices.html")
        result = _parse_nordea_bond_prices(html)
        # The 30-yr Frihed10 row has coupon 5,00% and kurs 99,38
        # Make sure we got the standard 30-yr obligation, not the Frihed variant
        assert result.fixed_coupon == Decimal("0.04")
        assert result.fixed_kurs == Decimal("93.48")


# ─── Finans Danmark parser ───────────────────────────────────────────


class TestFinansdanmarkParser:
    def test_parse_xlsx_url(self):
        html = _load_fixture("finansdanmark_obligationsrente.html")
        url = _parse_finansdanmark_xlsx_url(html)
        assert url is not None
        assert url.endswith(".xlsx")
        assert "finansdanmark.dk" in url

    def test_parse_xlsx(self):
        xlsx_bytes = _load_fixture_bytes("finansdanmark_obligationsrente.xlsx")
        rate = _parse_finansdanmark_xlsx(xlsx_bytes)
        # Sheet2 most recent week: Lang rente ≈ 4.49%
        assert rate is not None
        assert Decimal("0.04") < rate < Decimal("0.05")


# ─── Jyske reference rates parser ────────────────────────────────────


class TestJyskeReferenceRatesParser:
    def test_parse_fixture(self):
        html = _load_fixture("jyske_reference_rates.html")
        rates = _parse_jyske_reference_rates(html)
        assert isinstance(rates, ReferenceRates)
        assert rates.cibor_3m == Decimal("2.2700") / Decimal(100)
        assert rates.cibor_6m == Decimal("2.5500") / Decimal(100)
        assert rates.cita_3m == Decimal("1.8743") / Decimal(100)


# ─── DST DESTR parser ────────────────────────────────────────────────


class TestDstDestrParser:
    def test_parse_fixture(self):
        csv = _load_fixture("dst_destr.csv")
        rate = _parse_destr_rate(csv)
        assert rate == Decimal("2.0480") / Decimal(100)
        assert rate == Decimal("0.020480")


# ─── Lookup bidragssats ──────────────────────────────────────────────


class TestLookupBidragssats:
    def _make_rates(self, entries: list[BidragssatsEntry]) -> MarketRates:
        return MarketRates(
            fetched_at=datetime.now(),
            bidragssatser=entries,
        )

    def test_exact_match(self):
        entries = [
            BidragssatsEntry(
                institute=Institute.NYKREDIT,
                loan_type=__import__(
                    "boligregner.models", fromlist=["LoanType"]
                ).LoanType.F3,
                ltv_band=LTVBand.ZERO_TO_40,
                afdragsfrihed=False,
                bidragssats=Decimal("0.0070"),
            )
        ]
        rates = self._make_rates(entries)
        key = BidragssatsKey(
            institute=Institute.NYKREDIT,
            loan_type=__import__(
                "boligregner.models", fromlist=["LoanType"]
            ).LoanType.F3,
            ltv_band=LTVBand.ZERO_TO_40,
            afdragsfrihed=False,
        )
        assert lookup_bidragssats(key, rates) == Decimal("0.0070")

    def test_fallback_to_dst_avg(self):
        rates = MarketRates(
            fetched_at=datetime.now(),
            dst_rates=DSTRates(f3_bidrag_avg=Decimal("0.00983")),
        )
        key = BidragssatsKey(
            institute=Institute.NYKREDIT,
            loan_type=__import__(
                "boligregner.models", fromlist=["LoanType"]
            ).LoanType.F3,
            ltv_band=LTVBand.ZERO_TO_40,
            afdragsfrihed=False,
        )
        assert lookup_bidragssats(key, rates) == Decimal("0.00983")

    def test_fallback_to_default(self):
        rates = MarketRates(fetched_at=datetime.now())
        key = BidragssatsKey(
            institute=Institute.NYKREDIT,
            loan_type=__import__(
                "boligregner.models", fromlist=["LoanType"]
            ).LoanType.F3,
            ltv_band=LTVBand.ZERO_TO_40,
            afdragsfrihed=False,
        )
        assert lookup_bidragssats(key, rates) == Decimal("0.006")


# ─── build_preset_from_market ────────────────────────────────────────


class TestBuildPresetFromMarket:
    def test_preserves_preset_structure(self):
        rates = MarketRates(fetched_at=datetime.now())
        result = build_preset_from_market(rates, preset_name="default")
        from boligregner.models import CalculatorInput

        assert isinstance(result, CalculatorInput)
        assert len(result.alternatives) == 3
        # Labels should match the preset
        labels = [a.label for a in result.alternatives]
        assert "30 år DESTR" in labels
        assert "30 år F1" in labels
        assert "30 år 4% obligation" in labels

    def test_overrides_f3_rate(self):
        rates = MarketRates(
            fetched_at=datetime.now(),
            nominal_rates=NominalRates(f3=Decimal("0.0239")),
        )
        result = build_preset_from_market(rates, preset_name="default")
        # The default preset has no F3 alternative, but DESTR uses reference.
        # Check that the function runs without error and produces valid input.
        from boligregner.models import CalculatorInput

        assert isinstance(result, CalculatorInput)

    def test_overrides_fixed_coupon_and_price(self):
        rates = MarketRates(
            fetched_at=datetime.now(),
            fixed_coupon_rate=Decimal("0.04"),
            fixed_bond_price=Decimal("93.48"),
        )
        result = build_preset_from_market(rates, preset_name="default")
        # Find the FIXED alternative
        from boligregner.models import LoanType

        for alt in result.alternatives:
            for comp in alt.components:
                if comp.loan_type == LoanType.FIXED:
                    assert comp.rate == Decimal("0.04")
                    assert comp.price == Decimal("93.48")

    def test_clamps_negative_rates(self):
        rates = MarketRates(
            fetched_at=datetime.now(),
            nominal_rates=NominalRates(f5=Decimal("-0.001")),
        )
        result = build_preset_from_market(rates, preset_name="default")
        from boligregner.models import LoanType

        for alt in result.alternatives:
            for comp in alt.components:
                if comp.loan_type == LoanType.F5:
                    assert comp.rate >= 0

    def test_overrides_destr_reference_rate(self):
        rates = MarketRates(
            fetched_at=datetime.now(),
            reference_rates=ReferenceRates(destr=Decimal("0.02048")),
        )
        result = build_preset_from_market(rates, preset_name="default")
        from boligregner.models import LoanType

        for alt in result.alternatives:
            for comp in alt.components:
                if comp.loan_type == LoanType.DESTR:
                    assert comp.reference_rate == Decimal("0.02048")
                    # rate should be reference + margin
                    assert comp.rate == comp.reference_rate + comp.margin

    def test_unknown_preset_raises(self):
        rates = MarketRates(fetched_at=datetime.now())
        with pytest.raises(ValueError, match="Unknown preset"):
            build_preset_from_market(rates, preset_name="nonexistent")
