"""Tester for markdown- og JSON-utgaven av oppdateringen."""

import json

import pytest
from daily_brief.render import _num, _pct, _signed, render_json, render_markdown


def test_norwegian_number_format():
    """Tall skrives med mellomrom som tusenskille og komma som desimaltegn."""
    assert _num(116404.09) == "116 404,09"
    assert _num(1234567.5, 1) == "1 234 567,5"
    assert _num(0.5) == "0,50"


def test_signed_values_use_minus_sign():
    """Negative tall bruker minustegn, ikke bindestrek."""
    assert _signed(1619.93) == "+1 619,93"
    assert _signed(-878.77) == "−878,77"
    assert _pct(-0.76) == "−0,76 %"


def test_markdown_has_all_sections(brief):
    """Rapporten inneholder alle de faste delene."""
    markdown = render_markdown(brief)

    for heading in ("## Porteføljen", "### Posisjoner", "## Marked", "## Selskapene",
                    "## Kalender", "## Forbehold"):
        assert heading in markdown


def test_markdown_lists_every_position(brief, config):
    """Alle åtte posisjonene står i tabellen og har hvert sitt avsnitt."""
    markdown = render_markdown(brief)

    for position in config.positions:
        assert f"**{position.symbol}**" in markdown
        assert f"### {position.symbol} — {position.name}" in markdown


def test_markdown_splits_stock_and_fx(brief):
    """Toppsammendraget viser hva som var aksjebevegelse og hva som var valuta."""
    markdown = render_markdown(brief)

    assert "Aksjebevegelse:" in markdown
    assert "Valutaeffekt (USDNOK" in markdown


def test_markdown_shows_market_groups(brief):
    """Markedsoversikten grupperer indekser, renter, valuta og råvarer."""
    markdown = render_markdown(brief)

    for heading in ("### Indekser", "### Renter", "### Valuta", "### Råvarer"):
        assert heading in markdown

    assert "S&P 500" in markdown
    assert "Brent-olje" in markdown
    # Renter oppgis i basispunkter, ikke prosentvis endring av nivået.
    assert "bp" in markdown


def test_markdown_includes_fresh_news_only(brief):
    """Ferske saker kommer med, gamle faller ut av vinduet."""
    markdown = render_markdown(brief)

    assert "Tesla utvider produksjonen ved Giga Berlin" in markdown
    # SoFi-saken er fire døgn gammel og skal være filtrert bort.
    assert "SoFi passerer ti millioner kunder" not in markdown


def test_markdown_shows_summary_and_source_link(brief):
    """Hver nyhet får et sammendrag og en synlig lenke til kilden."""
    markdown = render_markdown(brief)

    assert "Tesla utvider kapasiteten ved fabrikken utenfor Berlin" in markdown
    assert "Kilde: <https://example.com/tesla-giga-berlin>" in markdown


def test_markdown_handles_news_without_summary(brief):
    """En sak uten sammendrag får fortsatt tittel og kildelenke."""
    markdown = render_markdown(brief)

    assert "Meta kjøper opp mindre AI-selskap" in markdown
    assert "Kilde: <https://example.com/meta-oppkjop>" in markdown


def test_json_carries_summaries(brief):
    """Sammendragene ligger i JSON, så leveringslaget slipper å finne på egne."""
    payload = render_json(brief)
    by_url = {n["url"]: n for n in payload["news"]}

    assert by_url["https://example.com/mp-avtale"]["summary"].startswith(
        "MP Materials har inngått"
    )
    assert by_url["https://example.com/meta-oppkjop"]["summary"] is None


def test_markdown_translates_recommendations(brief):
    """Analytikeranbefalinger vises på norsk."""
    markdown = render_markdown(brief)

    assert "Sterkt kjøp" in markdown
    assert "strong_buy" not in markdown


def test_calendar_only_covers_portfolio(brief):
    """Kalenderen tar med porteføljens selskaper, ikke resten av markedet."""
    markdown = render_markdown(brief)
    calendar = markdown.split("## Kalender")[1].split("## Forbehold")[0]

    assert "CEG legger fram kvartalstall" in calendar
    assert "SOFI legger fram kvartalstall" in calendar
    # NVDA står i kalenderen fra kilden, men eies ikke.
    assert "NVDA" not in calendar


def test_footer_documents_derived_shares(brief):
    """Utledede antall aksjer oppgis, slik at de kan etterprøves."""
    markdown = render_markdown(brief)
    footer = markdown.split("## Forbehold")[1]

    assert "Antall aksjer er utledet" in footer
    assert "KO 24,1403" in footer
    assert "ikke investeringsrådgivning" in footer


def test_json_is_serialisable_and_complete(brief):
    """JSON-utgaven kan skrives til fil og har alt rutinen trenger."""
    payload = render_json(brief)
    round_tripped = json.loads(json.dumps(payload, ensure_ascii=False))

    assert set(round_tripped) >= {
        "generated_at", "portfolio", "fx", "positions", "market", "news", "events",
    }
    assert len(round_tripped["positions"]) == 8
    assert round_tripped["portfolio"]["day_change_nok"] == pytest.approx(
        brief.valuation.day_change_nok, abs=0.01
    )
    assert round_tripped["fx"]["usdnok"] == pytest.approx(10.05)


def test_json_decomposition_matches_markdown(brief):
    """Tallene i JSON er de samme som i markdown-rapporten."""
    payload = render_json(brief)
    portfolio = payload["portfolio"]

    assert portfolio["stock_effect_nok"] + portfolio["fx_effect_nok"] == pytest.approx(
        portfolio["day_change_nok"], abs=0.01
    )


def test_issues_are_surfaced(brief, bundle):
    """Kilder som feiler nevnes i rapporten framfor å forsvinne stille."""
    result, _ = bundle
    result.add_issue("kursmål", "HTTPError: 503")

    from daily_brief.pipeline import build_brief

    rebuilt = build_brief(brief.config, result, brief.valuation.fx, brief.generated_at)
    markdown = render_markdown(rebuilt)

    assert "Kilder som ikke svarte" in markdown
    assert "HTTPError: 503" in markdown
