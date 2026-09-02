"""Resultatdatoer, utbytte og analytikerkonsensus per selskap."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from .sources import FetchResult
from .utils import coerce_date

# Et anslag på neste ex-dato er bare troverdig hvis selskapet har en etablert rytme.
MIN_DIVIDENDS_FOR_ESTIMATE = 3
MIN_INTERVAL_DAYS = 60
MAX_INTERVAL_DAYS = 400


@dataclass(frozen=True)
class EarningsEvent:
    """Kommende kvartalsrapport."""

    symbol: str
    report_date: date
    eps_consensus: float | None

    def days_until(self, today: date) -> int:
        """Antall dager til rapporten."""
        return (self.report_date - today).days


@dataclass(frozen=True)
class DividendInfo:
    """Siste utbytte og et anslag på neste ex-dato."""

    symbol: str
    last_ex_date: date
    last_amount: float
    trailing_12m: float
    estimated_next_ex_date: date | None

    def yield_pct(self, price: float | None) -> float | None:
        """Direkteavkastning siste tolv måneder, målt mot dagens kurs."""
        if not price:
            return None
        return self.trailing_12m / price * 100.0


@dataclass(frozen=True)
class AnalystView:
    """Analytikernes samlede syn på selskapet."""

    symbol: str
    recommendation: str | None
    recommendation_mean: float | None
    number_of_analysts: int | None
    target_consensus: float | None
    target_high: float | None
    target_low: float | None

    def upside_pct(self, price: float | None) -> float | None:
        """Avstand fra dagens kurs opp til konsensus-kursmålet, i prosent."""
        if not price or not self.target_consensus:
            return None
        return (self.target_consensus / price - 1.0) * 100.0


@dataclass(frozen=True)
class CompanyEvents:
    """Alt som er verdt å vite om ett selskap utover kursen."""

    symbol: str
    earnings: EarningsEvent | None
    dividend: DividendInfo | None
    analyst: AnalystView | None


def _build_earnings(result: FetchResult, symbols: Sequence[str]) -> dict[str, EarningsEvent]:
    """Plukk porteføljens selskaper ut av resultatkalenderen."""
    wanted = {symbol.upper() for symbol in symbols}
    earnings: dict[str, EarningsEvent] = {}

    for record in result.earnings:
        symbol = str(record.get("symbol") or "").upper()
        report_date = coerce_date(record.get("report_date"))

        if symbol not in wanted or report_date is None:
            continue

        # Kalenderen kan liste flere datoer for samme selskap; den nærmeste gjelder.
        existing = earnings.get(symbol)
        if existing is not None and existing.report_date <= report_date:
            continue

        consensus = record.get("eps_consensus")
        earnings[symbol] = EarningsEvent(
            symbol=symbol,
            report_date=report_date,
            eps_consensus=float(consensus) if consensus is not None else None,
        )

    return earnings


def _estimate_next_ex_date(ex_dates: Sequence[date]) -> date | None:
    """Anslå neste ex-dato ut fra selskapets historiske utbytterytme."""
    if len(ex_dates) < MIN_DIVIDENDS_FOR_ESTIMATE:
        return None

    recent = sorted(ex_dates)[-5:]
    intervals = [
        (later - earlier).days for earlier, later in zip(recent, recent[1:])
    ]

    if not intervals:
        return None

    typical = median(intervals)

    if not MIN_INTERVAL_DAYS <= typical <= MAX_INTERVAL_DAYS:
        return None

    return recent[-1] + timedelta(days=int(typical))


def _build_dividends(result: FetchResult, today: date) -> dict[str, DividendInfo]:
    """Sett sammen utbytteinformasjon per ticker."""
    dividends: dict[str, DividendInfo] = {}

    for symbol, records in result.dividends.items():
        parsed = [
            (coerce_date(record.get("ex_dividend_date")), record.get("amount"))
            for record in records
        ]
        history = [
            (ex_date, float(amount))
            for ex_date, amount in parsed
            if ex_date is not None and amount is not None
        ]

        if not history:
            continue

        history.sort()
        last_ex_date, last_amount = history[-1]
        year_ago = today - timedelta(days=365)
        trailing = sum(
            amount for ex_date, amount in history if ex_date >= year_ago
        )

        dividends[symbol] = DividendInfo(
            symbol=symbol,
            last_ex_date=last_ex_date,
            last_amount=last_amount,
            trailing_12m=trailing,
            estimated_next_ex_date=_estimate_next_ex_date(
                [ex_date for ex_date, _ in history]
            ),
        )

    return dividends


def _build_analysts(result: FetchResult) -> dict[str, AnalystView]:
    """Sett sammen analytikerkonsensus per ticker."""
    analysts: dict[str, AnalystView] = {}

    for symbol, record in result.targets.items():
        analysts[symbol] = AnalystView(
            symbol=symbol,
            recommendation=record.get("recommendation"),
            recommendation_mean=record.get("recommendation_mean"),
            number_of_analysts=record.get("number_of_analysts"),
            target_consensus=record.get("target_consensus"),
            target_high=record.get("target_high"),
            target_low=record.get("target_low"),
        )

    return analysts


def build_events(
    result: FetchResult, symbols: Sequence[str], today: date
) -> dict[str, CompanyEvents]:
    """Samle resultatdato, utbytte og analytikersyn for hvert selskap."""
    earnings = _build_earnings(result, symbols)
    dividends = _build_dividends(result, today)
    analysts = _build_analysts(result)

    return {
        symbol: CompanyEvents(
            symbol=symbol,
            earnings=earnings.get(symbol),
            dividend=dividends.get(symbol),
            analyst=analysts.get(symbol),
        )
        for symbol in symbols
    }
