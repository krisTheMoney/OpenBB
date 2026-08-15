"""Rendering av oppdateringen til markdown og JSON."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .config import PortfolioConfig
from .events import CompanyEvents
from .market import MarketSnapshot
from .news import NewsItem
from .positions import PortfolioValuation
from .sources import SourceIssue

WEEKDAYS = (
    "mandag",
    "tirsdag",
    "onsdag",
    "torsdag",
    "fredag",
    "lørdag",
    "søndag",
)

MONTHS = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)

RECOMMENDATION_LABELS = {
    "strong_buy": "Sterkt kjøp",
    "buy": "Kjøp",
    "hold": "Hold",
    "sell": "Salg",
    "strong_sell": "Sterkt salg",
    "underperform": "Underperform",
    "outperform": "Outperform",
    "none": "Ingen dekning",
}

CALENDAR_HORIZON_DAYS = 14


@dataclass(frozen=True)
class Brief:
    """Alt som skal med i én daglig oppdatering."""

    generated_at: datetime
    trading_day: date | None
    config: PortfolioConfig
    valuation: PortfolioValuation
    market: MarketSnapshot
    events: dict[str, CompanyEvents]
    news: dict[str, tuple[NewsItem, ...]]
    issues: tuple[SourceIssue, ...]


def _num(value: float, decimals: int = 2) -> str:
    """Skriv et tall på norsk: mellomrom som tusenskille, komma som desimaltegn."""
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", " ")


def _signed(value: float, decimals: int = 2) -> str:
    """Skriv et tall med eksplisitt fortegn."""
    return f"{'+' if value >= 0 else '−'}{_num(abs(value), decimals)}"


def _pct(value: float, decimals: int = 2) -> str:
    """Skriv en prosentverdi med fortegn."""
    return f"{_signed(value, decimals)} %"


def _norwegian_date(value: date) -> str:
    """Skriv datoen som 'fredag 14. august 2026'."""
    return (
        f"{WEEKDAYS[value.weekday()]} {value.day}. "
        f"{MONTHS[value.month - 1]} {value.year}"
    )


def _short_date(value: date) -> str:
    """Skriv datoen som '14. aug'."""
    return f"{value.day}. {MONTHS[value.month - 1][:3]}"


def _recommendation(value: str | None) -> str | None:
    """Oversett analytikeranbefalingen til norsk."""
    if not value:
        return None
    return RECOMMENDATION_LABELS.get(str(value).lower().replace(" ", "_"), str(value))


def _summary_section(brief: Brief) -> list[str]:
    """Toppsammendraget: verdi, dagens endring og hva som drev den."""
    valuation = brief.valuation

    lines = [
        "## Porteføljen",
        "",
        f"**NOK {_num(valuation.total_value_nok)}** "
        f"· dagen **{_signed(valuation.day_change_nok)} NOK "
        f"({_pct(valuation.day_change_pct)})**",
        "",
        f"- Aksjebevegelse: {_signed(valuation.stock_effect_nok)} NOK",
        f"- Valutaeffekt (USDNOK {_pct(valuation.fx.day_change_pct)}): "
        f"{_signed(valuation.fx_effect_nok)} NOK",
        f"- Urealisert siden kjøp: {_signed(valuation.total_unrealized_pl_nok)} NOK "
        f"({_pct(valuation.total_unrealized_pl_pct)})",
        f"- Kontanter: NOK {_num(valuation.cash_nok)}",
        "",
        "Hele porteføljen er notert i USD, så USDNOK slår rett inn på avkastningen "
        "i kroner. Linjene over deler dagens endring i de to driverne.",
        "",
    ]

    return lines


def _positions_table(brief: Brief) -> list[str]:
    """Posisjonstabellen, sortert etter dagens bidrag i kroner."""
    lines = [
        "### Posisjoner",
        "",
        "| Ticker | Kurs (USD) | Dag | Verdi (NOK) | Bidrag (NOK) | Avkastning | Vekt |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for position in brief.valuation.by_day_contribution:
        lines.append(
            f"| **{position.symbol}** "
            f"| {_num(position.price)} "
            f"| {_pct(position.day_change_pct)} "
            f"| {_num(position.market_value_nok)} "
            f"| {_signed(position.day_change_nok)} "
            f"| {_signed(position.unrealized_pl_nok)} "
            f"({_pct(position.unrealized_pl_pct, 1)}) "
            f"| {_num(position.weight_pct, 1)} % |"
        )

    if brief.valuation.unavailable:
        missing = ", ".join(
            f"{item.symbol} ({item.reason})" for item in brief.valuation.unavailable
        )
        lines += ["", f"Uten kursdata i dag: {missing}."]

    lines.append("")
    return lines


def _market_section(brief: Brief) -> list[str]:
    """Markedsoversikten, gruppert etter instrumenttype."""
    lines = ["## Marked", ""]

    groups = (
        ("indeks", "Indekser", "nivå"),
        ("rente", "Renter", "nivå"),
        ("valuta", "Valuta", "kurs"),
        ("råvare", "Råvarer", "pris"),
    )

    for category, heading, _ in groups:
        moves = brief.market.by_category(category)

        if not moves:
            continue

        lines += [f"### {heading}", "", "| Instrument | Nivå | Endring |", "| --- | ---: | ---: |"]

        for move in moves:
            if category == "rente":
                change = f"{_signed(move.change_bp, 1)} bp"
                level = f"{_num(move.last, 2)} %"
            elif category == "valuta":
                change = _pct(move.change_pct)
                level = _num(move.last, 4)
            else:
                change = _pct(move.change_pct)
                level = _num(move.last)

            lines.append(f"| {move.label} | {level} | {change} |")

        lines.append("")

    if not brief.market.moves:
        lines += ["Ingen markedsdata tilgjengelig i denne kjøringen.", ""]

    return lines


def _company_block(brief: Brief, symbol: str, name: str, price: float | None) -> list[str]:
    """Detaljene for ett selskap: analytikere, resultat, utbytte og nyheter."""
    lines = [f"### {symbol} — {name}", ""]
    events = brief.events.get(symbol)
    today = brief.generated_at.date()

    if events and events.analyst:
        analyst = events.analyst
        parts = []
        label = _recommendation(analyst.recommendation)

        if label:
            count = (
                f" ({analyst.number_of_analysts} analytikere)"
                if analyst.number_of_analysts
                else ""
            )
            parts.append(f"{label}{count}")

        if analyst.target_consensus:
            upside = analyst.upside_pct(price)
            suffix = f", {_pct(upside, 1)} fra dagens kurs" if upside is not None else ""
            parts.append(f"kursmål {_num(analyst.target_consensus)} USD{suffix}")

        if parts:
            lines.append(f"- **Analytikere:** {' · '.join(parts)}")

    if events and events.earnings:
        earnings = events.earnings
        days = earnings.days_until(today)
        when = "i dag" if days == 0 else f"om {days} dager" if days > 0 else "nylig"
        consensus = (
            f", konsensus {_num(earnings.eps_consensus)} USD per aksje"
            if earnings.eps_consensus is not None
            else ""
        )
        lines.append(
            f"- **Resultat:** {_short_date(earnings.report_date)} ({when}){consensus}"
        )

    if events and events.dividend:
        dividend = events.dividend
        yield_pct = dividend.yield_pct(price)
        yield_text = (
            f", {_num(yield_pct, 2)} % siste tolv måneder" if yield_pct else ""
        )
        estimate = (
            f" Anslått neste ex-dato {_short_date(dividend.estimated_next_ex_date)}."
            if dividend.estimated_next_ex_date
            else ""
        )
        lines.append(
            f"- **Utbytte:** {_num(dividend.last_amount)} USD "
            f"(ex-dato {_short_date(dividend.last_ex_date)}){yield_text}.{estimate}"
        )

    items = brief.news.get(symbol, ())

    if items:
        lines.append("- **Nyheter:**")
        for item in items:
            age = int(item.age_hours(brief.generated_at))
            source = f" — {item.source}" if item.source else ""
            link = f"[{item.title}]({item.url})" if item.url else item.title
            lines.append(f"  - {link}{source}, {age} t siden")

    if len(lines) == 2:
        lines.append("- Ingen nye hendelser å melde.")

    lines.append("")
    return lines


def _companies_section(brief: Brief) -> list[str]:
    """Ett avsnitt per selskap, i samme rekkefølge som posisjonstabellen."""
    lines = ["## Selskapene", ""]
    prices = {p.symbol: p.price for p in brief.valuation.positions}
    names = {p.symbol: p.name for p in brief.config.positions}

    for position in brief.valuation.by_day_contribution:
        lines += _company_block(
            brief, position.symbol, position.name, prices.get(position.symbol)
        )

    for item in brief.valuation.unavailable:
        lines += _company_block(brief, item.symbol, names.get(item.symbol, item.symbol), None)

    return lines


def _calendar_section(brief: Brief) -> list[str]:
    """Kommende hendelser i porteføljen de neste to ukene."""
    today = brief.generated_at.date()
    entries: list[tuple[date, str]] = []

    for symbol, events in brief.events.items():
        if events.earnings:
            delta = (events.earnings.report_date - today).days
            if 0 <= delta <= CALENDAR_HORIZON_DAYS:
                entries.append(
                    (events.earnings.report_date, f"{symbol} legger fram kvartalstall")
                )

        if events.dividend and events.dividend.estimated_next_ex_date:
            ex_date = events.dividend.estimated_next_ex_date
            delta = (ex_date - today).days
            if 0 <= delta <= CALENDAR_HORIZON_DAYS:
                entries.append((ex_date, f"{symbol} anslått ex-dato for utbytte"))

    if not entries:
        return [
            "## Kalender",
            "",
            f"Ingen kjente hendelser i porteføljen de neste {CALENDAR_HORIZON_DAYS} dagene.",
            "",
        ]

    entries.sort()
    lines = ["## Kalender", "", f"Neste {CALENDAR_HORIZON_DAYS} dager:", ""]
    lines += [
        f"- **{_short_date(event_date)}** — {description}"
        for event_date, description in entries
    ]
    lines.append("")

    return lines


def _footer_section(brief: Brief) -> list[str]:
    """Forbehold, avledede tall og eventuelle kilder som feilet."""
    derived = [p for p in brief.valuation.positions if p.shares_derived]

    lines = ["## Forbehold", ""]

    if derived:
        lines.append(
            f"Antall aksjer er utledet fra avlesningen "
            f"{brief.config.snapshot_date.isoformat()} og USDNOK "
            f"{_num(brief.valuation.fx.snapshot, 4)} samme dag: "
            + ", ".join(f"{p.symbol} {_num(p.shares, 4)}" for p in derived)
            + ". Legg inn eksakte antall i portfolio.toml for å låse tallene."
        )
        lines.append("")

    if brief.issues:
        lines.append("Kilder som ikke svarte i denne kjøringen:")
        lines.append("")
        lines += [f"- {issue.source}: {issue.detail}" for issue in brief.issues]
        lines.append("")

    lines += [
        "Kursdata fra Yahoo Finance via OpenBB, resultatkalender fra Seeking Alpha. "
        "Kursene er sluttkurser, ikke sanntid.",
        "",
        "Dette er en datasammenstilling, ikke investeringsrådgivning.",
        "",
    ]

    return lines


def render_markdown(brief: Brief) -> str:
    """Sett sammen hele oppdateringen som markdown."""
    heading_date = brief.trading_day or brief.generated_at.date()

    lines = [
        f"# Daglig oppdatering — {_norwegian_date(heading_date)}",
        "",
        f"Generert {brief.generated_at.strftime('%Y-%m-%d %H:%M')} UTC.",
        "",
    ]

    lines += _summary_section(brief)
    lines += _positions_table(brief)
    lines += _market_section(brief)
    lines += _companies_section(brief)
    lines += _calendar_section(brief)
    lines += _footer_section(brief)

    return "\n".join(lines).rstrip() + "\n"


def render_json(brief: Brief) -> dict[str, Any]:
    """
    Bygg maskinlesbar utgave av oppdateringen.

    Dette er filen Claude-rutinen leser. Den skal inneholde alle tall rutinen trenger,
    slik at leveringslaget aldri må regne eller gjette selv.
    """
    valuation = brief.valuation

    return {
        "generated_at": brief.generated_at.isoformat(),
        "trading_day": brief.trading_day.isoformat() if brief.trading_day else None,
        "currency": brief.config.base_currency,
        "portfolio": {
            "total_value_nok": round(valuation.total_value_nok, 2),
            "cash_nok": round(valuation.cash_nok, 2),
            "day_change_nok": round(valuation.day_change_nok, 2),
            "day_change_pct": round(valuation.day_change_pct, 4),
            "stock_effect_nok": round(valuation.stock_effect_nok, 2),
            "fx_effect_nok": round(valuation.fx_effect_nok, 2),
            "unrealized_pl_nok": round(valuation.total_unrealized_pl_nok, 2),
            "unrealized_pl_pct": round(valuation.total_unrealized_pl_pct, 4),
            "cost_basis_nok": round(valuation.total_cost_basis_nok, 2),
        },
        "fx": {
            "usdnok": round(valuation.fx.now, 4),
            "usdnok_previous_close": round(valuation.fx.previous_close, 4),
            "usdnok_snapshot": round(valuation.fx.snapshot, 4),
            "usdnok_day_change_pct": round(valuation.fx.day_change_pct, 4),
        },
        "positions": [
            {
                "symbol": position.symbol,
                "name": position.name,
                "shares": round(position.shares, 6),
                "shares_derived": position.shares_derived,
                "price_usd": round(position.price, 4),
                "previous_close_usd": round(position.previous_close, 4),
                "day_change_pct": round(position.day_change_pct, 4),
                "market_value_nok": round(position.market_value_nok, 2),
                "day_change_nok": round(position.day_change_nok, 2),
                "stock_effect_nok": round(position.stock_effect_nok, 2),
                "fx_effect_nok": round(position.fx_effect_nok, 2),
                "cost_basis_nok": round(position.cost_basis_nok, 2),
                "unrealized_pl_nok": round(position.unrealized_pl_nok, 2),
                "unrealized_pl_pct": round(position.unrealized_pl_pct, 4),
                "weight_pct": round(position.weight_pct, 4),
            }
            for position in valuation.by_day_contribution
        ],
        "unavailable": [
            {"symbol": item.symbol, "name": item.name, "reason": item.reason}
            for item in valuation.unavailable
        ],
        "market": [
            {
                "symbol": move.symbol,
                "label": move.label,
                "category": move.category,
                "last": round(move.last, 4),
                "previous_close": round(move.previous, 4),
                "change": round(move.change, 4),
                "change_pct": round(move.change_pct, 4),
                "as_of": move.as_of.isoformat(),
            }
            for move in brief.market.moves
        ],
        "news": [
            {
                "symbol": symbol,
                "title": item.title,
                "url": item.url,
                "source": item.source,
                "published": item.published.isoformat(),
            }
            for symbol, items in brief.news.items()
            for item in items
        ],
        "events": [
            {
                "symbol": symbol,
                "earnings_date": (
                    events.earnings.report_date.isoformat() if events.earnings else None
                ),
                "eps_consensus": events.earnings.eps_consensus if events.earnings else None,
                "last_dividend_amount": (
                    events.dividend.last_amount if events.dividend else None
                ),
                "last_dividend_ex_date": (
                    events.dividend.last_ex_date.isoformat() if events.dividend else None
                ),
                "estimated_next_ex_date": (
                    events.dividend.estimated_next_ex_date.isoformat()
                    if events.dividend and events.dividend.estimated_next_ex_date
                    else None
                ),
                "recommendation": events.analyst.recommendation if events.analyst else None,
                "target_consensus": (
                    events.analyst.target_consensus if events.analyst else None
                ),
            }
            for symbol, events in brief.events.items()
        ],
        "issues": [
            {"source": issue.source, "detail": issue.detail} for issue in brief.issues
        ],
    }
