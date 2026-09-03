"""Tester for rensing og avkastningsberegning i fondshistorikken."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fund_history import _clean_price, _stats, _to_nok  # noqa: E402

START = date(2021, 9, 1)
END = date(2026, 9, 1)


def _series(prices, start=START):
    """Bygg en kursserie med én pris per dag."""
    return {start + timedelta(days=i): p for i, p in enumerate(prices)}


def test_nan_close_is_rejected():
    """NaN slipper gjennom en None-sjekk og må fanges eksplisitt."""
    assert _clean_price(float("nan")) is None


def test_infinite_and_nonpositive_are_rejected():
    """Uendelig og null gir meningsløs avkastning."""
    assert _clean_price(float("inf")) is None
    assert _clean_price(0) is None
    assert _clean_price(-5) is None


def test_none_and_junk_are_rejected():
    """Manglende eller ulesbare verdier skal ikke velte serien."""
    assert _clean_price(None) is None
    assert _clean_price("ikke et tall") is None


def test_valid_prices_pass_through():
    """Gyldige kurser slippes gjennom som float."""
    assert _clean_price(103.5) == pytest.approx(103.5)
    assert _clean_price("42") == pytest.approx(42.0)


def test_stats_computes_return_and_drawdown():
    """Totalavkastning, årlig snitt og største fall regnes fra kursserien."""
    # Fem år fra 100 til 200: dobling, med et fall til 80 underveis.
    days = 365 * 5
    series = {START: 100.0, START + timedelta(days=days // 2): 80.0,
              START + timedelta(days=days): 200.0}
    series.update({START + timedelta(days=i): 100.0 for i in range(1, 70)})

    stats = _stats(series, START, END)

    assert stats["total_return_pct"] == pytest.approx(100.0)
    assert stats["max_drawdown_pct"] == pytest.approx(-20.0)
    assert stats["cagr_pct"] == pytest.approx(14.87, abs=0.1)


def test_stats_needs_enough_observations():
    """En serie med få dager sier ingenting om fem års utvikling."""
    assert _stats(_series([100.0, 101.0, 102.0]), START, END) is None


def test_stats_flags_a_fund_younger_than_the_window():
    """Fond startet inne i perioden skal merkes, ikke sammenlignes som fem år."""
    # Starter to år inn i vinduet, men handler helt fram til slutten.
    first = END - timedelta(days=730)
    series = {first + timedelta(days=i): 100.0 + i * 0.05 for i in range(731)}

    stats = _stats(series, START, END)

    assert stats["covers_full_window"] is False
    assert stats["years_covered"] == pytest.approx(2.0, abs=0.05)


def test_stats_rejects_a_delisted_series():
    """
    En serie som stopper lenge før i dag hører til en nedlagt notering.

    Yahoo har fortsatt historikken, og den ser komplett ut helt til man ser på
    sluttdatoen — uten denne sjekken rapporteres en gammel treårsavkastning som fersk.
    """
    series = {START + timedelta(days=i): 100.0 + i * 0.05 for i in range(1180)}

    assert _stats(series, START, END) is None


def test_stats_tolerates_a_weekend_gap_at_the_end():
    """Siste kurs et par dager tilbake er helg, ikke en nedlagt notering."""
    series = {
        END - timedelta(days=730) + timedelta(days=i): 100.0 + i * 0.05
        for i in range(728)
    }

    assert _stats(series, START, END) is not None


def test_conversion_to_nok_uses_daily_rates():
    """Kursserien ganges med valutakursen dag for dag."""
    prices = _series([100.0, 110.0])
    rates = {"EUR": {START: 10.0, START + timedelta(days=1): 11.0}}

    converted = _to_nok(prices, "EUR", rates)

    assert converted[START] == pytest.approx(1000.0)
    assert converted[START + timedelta(days=1)] == pytest.approx(1210.0)


def test_pence_listings_are_divided_by_hundred():
    """Britiske noteringer i pence må bli pund før de blir kroner."""
    converted = _to_nok(_series([1000.0]), "GBX", {"GBP": {START: 13.0}})

    assert converted[START] == pytest.approx(130.0)


def test_missing_rate_falls_back_to_previous_day():
    """Mangler valutakursen på en handelsdag, brukes nærmeste foregående."""
    prices = _series([100.0, 100.0])
    rates = {"EUR": {START: 10.0}}

    converted = _to_nok(prices, "EUR", rates)

    assert converted[START + timedelta(days=1)] == pytest.approx(1000.0)


def test_unknown_currency_yields_nothing():
    """Uten valutakurs kan ingenting regnes om, og da skal serien være tom."""
    assert _to_nok(_series([100.0]), "JPY", {"EUR": {START: 10.0}}) == {}
