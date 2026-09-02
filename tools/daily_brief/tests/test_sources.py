"""Tester for behandlingen av rådata fra providerne, uten nettverk."""

from datetime import date

import pytest
from daily_brief.sources import (
    _as_records,
    _close_on_or_before,
    _last_two_closes,
    group_news_by_symbol,
)

SYMBOLS = ["KO", "JPM", "TSLA"]


class _FakeData:
    """Etterligner et pydantic-objekt fra en provider."""

    def __init__(self, **fields):
        self._fields = fields

    def model_dump(self):
        """Gi feltene tilbake som dict."""
        return dict(self._fields)


class _FakeAnnotated:
    """Etterligner en AnnotatedResult som pakker inn selve dataene."""

    def __init__(self, results):
        self.results = results


def test_records_unwrapped_from_models():
    """Pydantic-objekter gjøres om til vanlige dicts."""
    payload = [_FakeData(symbol="KO", close=1.0), {"symbol": "JPM", "close": 2.0}]

    assert _as_records(payload) == [
        {"symbol": "KO", "close": 1.0},
        {"symbol": "JPM", "close": 2.0},
    ]


def test_records_unwrapped_from_annotated_result():
    """Innpakkede resultater pakkes ut før de brukes."""
    payload = _FakeAnnotated([{"symbol": "KO"}])

    assert _as_records(payload) == [{"symbol": "KO"}]


def test_records_handles_empty_payload():
    """Et tomt svar gir en tom liste, ikke en feil."""
    assert _as_records(None) == []


def test_news_grouped_on_singular_symbol_field():
    """Provideren merker sakene med 'symbol', og det må treffe."""
    records = [
        {"symbol": "TSLA", "title": "En Tesla-sak"},
        {"symbol": "KO", "title": "En Coca-Cola-sak"},
    ]

    grouped = group_news_by_symbol(records, SYMBOLS)

    assert list(grouped) == ["TSLA", "KO"]
    assert grouped["TSLA"][0]["title"] == "En Tesla-sak"


def test_news_grouped_on_plural_symbols_field():
    """Standardmodellens 'symbols' må også treffe."""
    records = [{"symbols": "JPM,BAC", "title": "En banksak"}]

    grouped = group_news_by_symbol(records, SYMBOLS)

    assert grouped["JPM"][0]["title"] == "En banksak"


def test_news_for_unowned_tickers_is_dropped():
    """Saker om selskaper vi ikke eier tas ikke med."""
    records = [{"symbol": "NVDA", "title": "Ikke i porteføljen"}]

    assert group_news_by_symbol(records, SYMBOLS) == {}


def test_last_two_closes_picks_the_newest_pair():
    """Siste og nest siste sluttkurs plukkes ut, uansett rekkefølge inn."""
    records = [
        {"date": "2026-08-12", "close": 100.0},
        {"date": "2026-08-14", "close": 102.0},
        {"date": "2026-08-13", "close": 101.0},
    ]

    last, previous, as_of = _last_two_closes(records)

    assert last == pytest.approx(102.0)
    assert previous == pytest.approx(101.0)
    assert as_of == date(2026, 8, 14)


def test_last_two_closes_needs_two_points():
    """Én enkelt kurs er ikke nok til å regne en dagsendring."""
    assert _last_two_closes([{"date": "2026-08-14", "close": 102.0}]) is None


def test_last_two_closes_ignores_missing_values():
    """Rader uten sluttkurs hoppes over."""
    records = [
        {"date": "2026-08-12", "close": 100.0},
        {"date": "2026-08-13", "close": None},
        {"date": "2026-08-14", "close": 102.0},
    ]

    last, previous, _ = _last_two_closes(records)

    assert (last, previous) == (pytest.approx(102.0), pytest.approx(100.0))


def test_close_on_or_before_falls_back_to_previous_trading_day():
    """Er avlesningsdagen en helligdag, brukes nærmeste handelsdag før."""
    records = [
        {"date": "2026-08-13", "close": 10.01},
        {"date": "2026-08-14", "close": 10.02},
        {"date": "2026-08-17", "close": 10.09},
    ]

    # 15. og 16. august er helg, så fredagens kurs gjelder.
    assert _close_on_or_before(records, date(2026, 8, 16)) == pytest.approx(10.02)


def test_close_on_or_before_returns_none_when_too_early():
    """Ber vi om en dato før historikken starter, finnes det ingen kurs."""
    records = [{"date": "2026-08-14", "close": 10.02}]

    assert _close_on_or_before(records, date(2026, 1, 1)) is None
