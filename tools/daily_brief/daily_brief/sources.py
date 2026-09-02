"""
Henting av data fra OpenBB-providere.

Alt nettverksarbeid ligger her. Hver kilde er isolert: feiler én ticker eller ett
endepunkt, registreres det som et SourceIssue og resten av oppdateringen kjører videre.
En rapport med hull er langt bedre enn ingen rapport, så lenge hullene er synlige.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .positions import FxRates, Quote
from .utils import coerce_date

logger = logging.getLogger(__name__)

# Så mange kalenderdager tilbake vi ber om historikk for å garantert få med forrige
# handelsdag, selv over en langhelg med helligdager i begge ender.
HISTORY_LOOKBACK_DAYS = 12

USDNOK_SYMBOL = "USDNOK=X"
EURNOK_SYMBOL = "EURNOK=X"

# Instrumentene markedsoversikten dekker: amerikanske indekser, renter, valuta, råvarer.
MARKET_INSTRUMENTS: tuple[tuple[str, str, str], ...] = (
    ("^GSPC", "S&P 500", "indeks"),
    ("^IXIC", "Nasdaq Composite", "indeks"),
    ("^DJI", "Dow Jones", "indeks"),
    ("^VIX", "VIX", "indeks"),
    ("^TNX", "USA 10 år", "rente"),
    ("^FVX", "USA 5 år", "rente"),
    (USDNOK_SYMBOL, "USDNOK", "valuta"),
    (EURNOK_SYMBOL, "EURNOK", "valuta"),
    ("BZ=F", "Brent-olje", "råvare"),
    ("GC=F", "Gull", "råvare"),
)


@dataclass(frozen=True)
class SourceIssue:
    """En kilde som ikke svarte som forventet."""

    source: str
    detail: str


@dataclass
class FetchResult:
    """Samlet resultat fra alle kilder, med de problemene som oppsto underveis."""

    quotes: dict[str, Quote] = field(default_factory=dict)
    series: dict[str, tuple[float, float, date]] = field(default_factory=dict)
    news: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    earnings: list[dict[str, Any]] = field(default_factory=list)
    dividends: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[SourceIssue] = field(default_factory=list)

    def add_issue(self, source: str, detail: str) -> None:
        """Registrer at en kilde feilet, uten å avbryte kjøringen."""
        logger.warning("kilde '%s' feilet: %s", source, detail)
        self.issues.append(SourceIssue(source=source, detail=detail))


def _as_records(payload: Any) -> list[dict[str, Any]]:
    """Gjør svaret fra en fetcher om til vanlige dicts, uansett innpakning."""
    results = getattr(payload, "results", payload)

    if results is None:
        return []

    if not isinstance(results, (list, tuple)):
        results = [results]

    records: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, dict):
            records.append(item)
        elif hasattr(item, "model_dump"):
            records.append(item.model_dump())

    return records


async def _safe_fetch(
    result: FetchResult,
    source: str,
    fetcher: Callable[..., Any],
    params: dict[str, Any],
    benign: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """
    Kjør én fetcher og fang alt den måtte kaste.

    `benign` er tekstbiter som kjennetegner et forventet tomt svar framfor en feil —
    et selskap uten utbytte, for eksempel. De gir tom liste uten å havne i rapporten.
    """
    try:
        payload = await fetcher.fetch_data(params, {})
    except Exception as error:  # noqa: BLE001 - enhver feil skal isoleres til denne kilden
        message = str(error)
        if any(marker.lower() in message.lower() for marker in benign):
            logger.info("kilde '%s' hadde ingen data: %s", source, message)
            return []
        result.add_issue(source, f"{type(error).__name__}: {message}")
        return []

    return _as_records(payload)


def _last_two_closes(
    records: Sequence[dict[str, Any]],
) -> tuple[float, float, date] | None:
    """Trekk ut siste og nest siste sluttkurs fra en historikk-serie."""
    closes = [
        (record.get("date"), record.get("close"))
        for record in records
        if record.get("close") is not None
    ]

    if len(closes) < 2:
        return None

    closes.sort(key=lambda item: str(item[0]))
    (_, previous), (raw_as_of, last) = closes[-2], closes[-1]

    as_of = coerce_date(raw_as_of)
    if as_of is None:
        return None

    return float(last), float(previous), as_of


def _close_on_or_before(
    records: Sequence[dict[str, Any]], target: date
) -> float | None:
    """Finn sluttkursen på en gitt dato, eller nærmeste handelsdag før den."""
    candidates: list[tuple[date, float]] = []

    for record in records:
        close = record.get("close")
        parsed = coerce_date(record.get("date"))

        if close is None or parsed is None:
            continue

        if parsed <= target:
            candidates.append((parsed, float(close)))

    if not candidates:
        return None

    candidates.sort()
    return candidates[-1][1]


async def fetch_history(
    result: FetchResult, symbols: Sequence[str], start: date, end: date
) -> dict[str, list[dict[str, Any]]]:
    """
    Hent daglig historikk for en liste symboler.

    Indekser, valutakryss og futures behandles likt av Yahoo, så én kilde dekker alle.
    """
    from openbb_yfinance.models.equity_historical import YFinanceEquityHistoricalFetcher

    records = await _safe_fetch(
        result,
        "historikk",
        YFinanceEquityHistoricalFetcher,
        {
            "symbol": ",".join(symbols),
            "start_date": start,
            "end_date": end,
            "interval": "1d",
        },
    )

    grouped: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}

    for record in records:
        # Med flere symboler merker OpenBB hver rad med 'symbol'. Med ett symbol
        # utelates feltet, og da hører alle radene til det ene symbolet.
        symbol = record.get("symbol") or (symbols[0] if len(symbols) == 1 else None)
        if symbol in grouped:
            grouped[symbol].append(record)

    return grouped


async def fetch_market_series(result: FetchResult, today: date) -> None:
    """Hent siste to sluttkurser for hvert instrument i markedsoversikten."""
    symbols = [symbol for symbol, _, _ in MARKET_INSTRUMENTS]
    start = today - timedelta(days=HISTORY_LOOKBACK_DAYS)
    grouped = await fetch_history(result, symbols, start, today)

    for symbol in symbols:
        closes = _last_two_closes(grouped.get(symbol, []))
        if closes is None:
            result.add_issue(symbol, "for få sluttkurser til å regne dagsendring")
            continue
        result.series[symbol] = closes


async def fetch_fx_rates(
    result: FetchResult,
    snapshot_date: date,
    today: date,
    snapshot_override: float | None = None,
) -> FxRates | None:
    """
    Hent USDNOK nå, ved forrige close, og på avlesningsdagen.

    Den historiske kursen er det som gjør det mulig å utlede antall aksjer fra et
    skjermbilde som bare viser verdi i kroner. Megleren veksler til sin egen kurs,
    ikke Yahoos, så `snapshot_override` lar configen sette den kursen eksplisitt.
    """
    start = min(snapshot_date, today - timedelta(days=HISTORY_LOOKBACK_DAYS))
    grouped = await fetch_history(result, [USDNOK_SYMBOL], start, today)
    records = grouped.get(USDNOK_SYMBOL, [])

    closes = _last_two_closes(records)
    if closes is None:
        result.add_issue("USDNOK", "fant ikke dagens og gårsdagens kurs")
        return None

    now, previous, _ = closes

    if snapshot_override is not None:
        return FxRates(now=now, previous_close=previous, snapshot=snapshot_override)

    snapshot = _close_on_or_before(records, snapshot_date)

    if snapshot is None:
        result.add_issue(
            "USDNOK",
            f"fant ingen kurs på eller før avlesningsdagen {snapshot_date.isoformat()}",
        )
        return None

    return FxRates(now=now, previous_close=previous, snapshot=snapshot)


async def fetch_quotes(result: FetchResult, symbols: Sequence[str]) -> None:
    """Hent siste kurs og forrige close for porteføljens tickere."""
    from openbb_yfinance.models.equity_quote import YFinanceEquityQuoteFetcher

    records = await _safe_fetch(
        result,
        "kurser",
        YFinanceEquityQuoteFetcher,
        {"symbol": ",".join(symbols)},
    )

    for record in records:
        symbol = record.get("symbol")
        price = record.get("last_price")
        previous = record.get("prev_close")

        if symbol is None or price is None or previous is None:
            continue

        result.quotes[str(symbol).upper()] = Quote(
            symbol=str(symbol).upper(),
            price=float(price),
            previous_close=float(previous),
        )

    missing = [symbol for symbol in symbols if symbol not in result.quotes]
    if missing:
        result.add_issue("kurser", f"ingen kurs for: {', '.join(missing)}")


def group_news_by_symbol(
    records: Sequence[dict[str, Any]], symbols: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """
    Fordel nyhetssaker på tickerne de handler om.

    yfinance-provideren merker hver sak med 'symbol', mens standardmodellen bruker
    'symbols'. Begge må håndteres, ellers forsvinner alle nyhetene stille.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}

    for record in records:
        raw = record.get("symbol") or record.get("symbols") or ""
        tags = {part.strip().upper() for part in str(raw).split(",") if part.strip()}

        for symbol in symbols:
            if symbol.upper() in tags:
                grouped.setdefault(symbol, []).append(record)
                break

    return grouped


async def fetch_company_news(
    result: FetchResult, symbols: Sequence[str], limit: int = 8
) -> None:
    """Hent selskapsnyheter for hver ticker."""
    from openbb_yfinance.models.company_news import YFinanceCompanyNewsFetcher

    records = await _safe_fetch(
        result,
        "nyheter",
        YFinanceCompanyNewsFetcher,
        {"symbol": ",".join(symbols), "limit": limit},
    )

    result.news.update(group_news_by_symbol(records, symbols))


async def fetch_earnings_calendar(
    result: FetchResult, start: date, end: date
) -> None:
    """
    Hent resultatkalenderen for perioden.

    Nasdaq er hovedkilden: den er åpen og krever ingen nøkkel. Seeking Alpha står som
    reserve, men svarer ofte med en captcha-vegg på serverside-kall, så den prøves bare
    hvis Nasdaq ikke ga noe.
    """
    from openbb_nasdaq.models.calendar_earnings import NasdaqCalendarEarningsFetcher

    records = await _safe_fetch(
        result,
        "resultatkalender (Nasdaq)",
        NasdaqCalendarEarningsFetcher,
        {"start_date": start, "end_date": end},
    )

    if not records:
        from openbb_seeking_alpha.models.calendar_earnings import (
            SACalendarEarningsFetcher,
        )

        records = await _safe_fetch(
            result,
            "resultatkalender (Seeking Alpha)",
            SACalendarEarningsFetcher,
            {"start_date": start, "end_date": end},
        )

    result.earnings = records


async def fetch_dividends(
    result: FetchResult, symbols: Sequence[str], today: date
) -> None:
    """
    Hent utbyttehistorikk, brukt til å vise siste utbytte og anslå neste.

    Selskaper som ikke betaler utbytte gir ingen data. Det er normalt og skal ikke
    rapporteres som en kildefeil.
    """
    from openbb_yfinance.models.historical_dividends import (
        YFinanceHistoricalDividendsFetcher,
    )

    for symbol in symbols:
        records = await _safe_fetch(
            result,
            f"utbytte {symbol}",
            YFinanceHistoricalDividendsFetcher,
            {"symbol": symbol, "start_date": today - timedelta(days=550)},
            benign=("no dividend data",),
        )
        if records:
            result.dividends[symbol] = records


async def fetch_price_targets(result: FetchResult, symbols: Sequence[str]) -> None:
    """Hent analytikerkonsensus og kursmål."""
    from openbb_yfinance.models.price_target_consensus import (
        YFinancePriceTargetConsensusFetcher,
    )

    records = await _safe_fetch(
        result,
        "kursmål",
        YFinancePriceTargetConsensusFetcher,
        {"symbol": ",".join(symbols)},
    )

    for record in records:
        symbol = record.get("symbol")
        if symbol:
            result.targets[str(symbol).upper()] = record


async def collect(
    symbols: Sequence[str],
    snapshot_date: date,
    today: date | None = None,
    snapshot_usdnok: float | None = None,
) -> tuple[FetchResult, FxRates | None]:
    """
    Hent alt oppdateringen trenger.

    Kildene som ikke avhenger av hverandre hentes samtidig. Returnerer resultatet selv
    om enkeltkilder feilet — kall stedet må se på `issues`.
    """
    as_of = today or datetime.now(timezone.utc).date()
    result = FetchResult()

    fx_task = asyncio.create_task(
        fetch_fx_rates(result, snapshot_date, as_of, snapshot_usdnok)
    )

    await asyncio.gather(
        fetch_market_series(result, as_of),
        fetch_quotes(result, symbols),
        fetch_company_news(result, symbols),
        fetch_earnings_calendar(result, as_of, as_of + timedelta(days=14)),
        fetch_price_targets(result, symbols),
        fetch_dividends(result, symbols, as_of),
    )

    fx = await fx_task
    return result, fx
