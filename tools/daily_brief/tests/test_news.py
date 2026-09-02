"""Tester for filtrering og sammendrag av selskapsnyheter."""

from datetime import datetime, timedelta, timezone

from daily_brief.news import SUMMARY_MAX_CHARS, _clean_summary, build_news
from daily_brief.sources import FetchResult

NOW = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)


def _result(records):
    """Bygg et FetchResult med nyheter for én ticker."""
    result = FetchResult()
    result.news = {"KO": records}
    return result


def test_summary_whitespace_is_normalised():
    """Linjeskift og doble mellomrom fra kilden ryddes bort."""
    assert _clean_summary("  En  sak\nmed\trot ") == "En sak med rot"


def test_empty_summary_becomes_none():
    """Tomme sammendrag skal ikke bli til tomme avsnitt i rapporten."""
    assert _clean_summary("") is None
    assert _clean_summary(None) is None
    assert _clean_summary("   ") is None


def test_short_summary_is_left_alone():
    """Et sammendrag som allerede er kort røres ikke."""
    text = "Kort og greit."
    assert _clean_summary(text) == text


def test_long_summary_is_cut_at_a_sentence():
    """Lange sammendrag kuttes ved siste hele setning, ikke midt i en."""
    first = "A" * 200 + "."
    summary = _clean_summary(first + " " + "B" * 200 + ".")

    assert summary == first
    assert len(summary) <= SUMMARY_MAX_CHARS


def test_long_summary_without_sentence_end_is_ellipsised():
    """Finnes ingen setningsslutt å kutte ved, klippes siste ord bort."""
    summary = _clean_summary("ord " * 200)

    assert summary.endswith("…")
    assert len(summary) <= SUMMARY_MAX_CHARS + 1


def test_summary_is_read_from_provider_field():
    """Provideren legger sammendraget på 'summary'."""
    result = _result([
        {
            "title": "En sak",
            "url": "https://example.com/a",
            "date": (NOW - timedelta(hours=2)).isoformat(),
            "summary": "Sammendrag fra provideren.",
        }
    ])

    items = build_news(result, NOW)["KO"]

    assert items[0].summary == "Sammendrag fra provideren."


def test_summary_falls_back_to_excerpt():
    """Standardmodellen bruker 'excerpt' i stedet, og den må også fanges."""
    result = _result([
        {
            "title": "En sak",
            "url": "https://example.com/b",
            "date": (NOW - timedelta(hours=2)).isoformat(),
            "excerpt": "Sammendrag fra standardmodellen.",
        }
    ])

    items = build_news(result, NOW)["KO"]

    assert items[0].summary == "Sammendrag fra standardmodellen."


def test_item_without_summary_still_included():
    """En sak uten sammendrag skal med, bare uten sammendragslinje."""
    result = _result([
        {
            "title": "En sak uten sammendrag",
            "url": "https://example.com/c",
            "date": (NOW - timedelta(hours=2)).isoformat(),
        }
    ])

    items = build_news(result, NOW)["KO"]

    assert len(items) == 1
    assert items[0].summary is None


def test_stale_news_is_dropped():
    """Saker eldre enn vinduet faller ut."""
    result = _result([
        {
            "title": "Gammel sak",
            "url": "https://example.com/old",
            "date": (NOW - timedelta(hours=200)).isoformat(),
        }
    ])

    assert build_news(result, NOW) == {}


def test_duplicate_urls_are_collapsed():
    """Samme sak fra flere kilder skal bare telle én gang."""
    record = {
        "title": "Samme sak",
        "url": "https://example.com/same",
        "date": (NOW - timedelta(hours=3)).isoformat(),
    }

    items = build_news(_result([dict(record), dict(record)]), NOW)["KO"]

    assert len(items) == 1
