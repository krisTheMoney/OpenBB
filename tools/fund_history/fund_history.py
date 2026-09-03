#!/usr/bin/env python3
"""
Hent femårshistorikk for en liste UCITS-fond og regn avkastningen om til kroner.

Fondene er notert i EUR, USD eller britiske pence. En norsk investor tjener kroner,
så hele poenget her er å konvertere kursserien til NOK før avkastningen regnes ut —
ellers måler man fondets utvikling pluss en tilfeldig valutaeffekt man ikke ser.

Skriver resultatet som JSON. Kjøres av .github/workflows/fund-history.yml, siden
utviklingsmiljøet ikke slipper gjennom til Yahoo Finance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr
)
logger = logging.getLogger("fund_history")

YEARS = 5
LOOKBACK_DAYS = 365 * YEARS + 7

# Minste antall dagskurser før en serie regnes som brukbar.
MIN_ROWS = 60

# Fondene fra fondsoversikten. Yahoo lister samme fond på flere børser med ulike
# tickere, og hvilken som svarer varierer — derfor flere kandidater per fond, i
# prioritert rekkefølge. Første som gir nok historikk vinner.
FUNDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("VWCE", "Vanguard FTSE All-World", ("VWCE.DE", "VWCE.MI", "VWRA.L", "VWCE.F")),
    ("WEBN", "Amundi Prime All Country World", ("WEBN.DE", "WEBN.MI", "WEBN.F")),
    ("SPYI", "SPDR MSCI ACWI IMI", ("SPYI.DE", "IMIE.L", "SPYI.MI")),
    ("FWRA", "Invesco FTSE All-World", ("FWRA.L", "FWIA.DE", "FWRA.MI")),
    ("SPYY", "SPDR MSCI ACWI", ("SPYY.DE", "ACWI.L", "SPYY.MI")),
    ("SSAC", "iShares MSCI ACWI", ("SSAC.L", "IUSQ.DE", "ISAC.L")),
    ("VWRL", "Vanguard FTSE All-World, utbytte", ("VWRL.AS", "VWRL.L", "VGWL.DE")),
    ("IWDA", "iShares Core MSCI World", ("IWDA.AS", "EUNL.DE", "SWDA.L")),
    ("PRIW", "Amundi Prime Global", ("PRIW.DE", "GLOB.MI", "PRAW.DE")),
    ("SWRD", "SPDR MSCI World", ("SWRD.L", "SPPW.DE", "SWRD.MI")),
    ("XDWD", "Xtrackers MSCI World", ("XDWD.DE", "XDWD.L", "XMWO.MI")),
    ("VHVE", "Vanguard FTSE Developed World", ("VHVE.L", "VGVE.DE", "VHVE.MI")),
    ("HMWO", "HSBC MSCI World", ("HMWO.L", "H4ZJ.DE", "HMWO.MI")),
    ("EIMI", "iShares Core MSCI EM IMI", ("EIMI.L", "IS3N.DE", "EIMI.MI")),
    ("WSML", "iShares MSCI World Small Cap", ("WSML.L", "IUSN.DE", "WSML.MI")),
    ("XMEU", "Xtrackers MSCI Europe", ("XMEU.DE", "XMEU.L", "XMEU.MI")),
    ("SPEQ", "Invesco S&P 500 Equal Weight", ("SPEQ.L", "XDEW.DE", "EWSP.L")),
    ("SPYL", "SPDR S&P 500", ("SPYL.DE", "SPYL.MI", "SPY5.L")),
    ("CSPX", "iShares Core S&P 500", ("CSPX.L", "SXR8.DE", "CSPX.MI")),
    ("VUAA", "Vanguard S&P 500", ("VUAA.L", "VUAA.DE", "VUSA.AS")),
)

# Referanser å måle fondene mot: verdensindeksen og aksjene brukeren allerede eier.
BENCHMARKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("^GSPC", "S&P 500 (indeks, USD)", ("^GSPC",)),
    ("KO", "Coca-Cola", ("KO",)),
    ("JPM", "JPMorgan Chase", ("JPM",)),
    ("TSLA", "Tesla", ("TSLA",)),
    ("GOOG", "Alphabet", ("GOOG",)),
    ("META", "Meta Platforms", ("META",)),
    ("MP", "MP Materials", ("MP",)),
    ("CEG", "Constellation Energy", ("CEG",)),
    ("SOFI", "SoFi Technologies", ("SOFI",)),
)

FX_SYMBOLS = {"USD": "USDNOK=X", "EUR": "EURNOK=X", "GBP": "GBPNOK=X"}


def _coerce_date(value: Any) -> date | None:
    """Tolk en dato fra provideren, som kan gi date, datetime eller streng."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _clean_price(value: Any) -> float | None:
    """
    Gjør en sluttkurs om til et brukbart tall.

    Yahoo returnerer rader med NaN for dager børsen var stengt, og NaN slipper gjennom
    en vanlig None-sjekk. Én slik rad først i serien er nok til å gjøre hele
    avkastningsberegningen til NaN, så de må lukes ut her.
    """
    if value is None:
        return None

    try:
        price = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(price) or math.isinf(price) or price <= 0:
        return None

    return price


def _records(payload: Any) -> list[dict[str, Any]]:
    """Pakk ut svaret fra en fetcher til vanlige dicts."""
    results = getattr(payload, "results", payload)
    if results is None:
        return []
    if not isinstance(results, (list, tuple)):
        results = [results]
    out = []
    for item in results:
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "model_dump"):
            out.append(item.model_dump())
    return out


async def _history(symbol: str, start: date, end: date) -> dict[date, float]:
    """Hent daglige sluttkurser for ett symbol."""
    from openbb_yfinance.models.equity_historical import YFinanceEquityHistoricalFetcher

    try:
        payload = await YFinanceEquityHistoricalFetcher.fetch_data(
            {"symbol": symbol, "start_date": start, "end_date": end, "interval": "1d"},
            {},
        )
    except Exception as error:  # noqa: BLE001 - én ticker som feiler skal ikke stoppe resten
        logger.info("ingen historikk for %s: %s", symbol, error)
        return {}

    series: dict[date, float] = {}
    for record in _records(payload):
        day = _coerce_date(record.get("date"))
        close = _clean_price(record.get("close"))
        if day is not None and close is not None:
            series[day] = close

    return series


async def _currency(symbol: str) -> str | None:
    """Finn hvilken valuta et fond er notert i."""
    from openbb_yfinance.models.equity_quote import YFinanceEquityQuoteFetcher

    try:
        payload = await YFinanceEquityQuoteFetcher.fetch_data({"symbol": symbol}, {})
    except Exception as error:  # noqa: BLE001
        logger.info("fant ikke valuta for %s: %s", symbol, error)
        return None

    for record in _records(payload):
        currency = record.get("currency")
        if currency:
            return str(currency).upper()

    return None


def _to_nok(
    series: dict[date, float], currency: str, fx: dict[str, dict[date, float]]
) -> dict[date, float]:
    """
    Regn en kursserie om til kroner.

    Britiske noteringer oppgis i pence, ikke pund, så de må deles på hundre først.
    Er fondet notert i kroner allerede, returneres serien uendret.
    """
    if currency == "NOK":
        return dict(series)

    divisor = 1.0
    base = currency

    if currency in ("GBP", "GBX", "GBp"):
        base = "GBP"
        divisor = 100.0 if currency in ("GBX", "GBp") else 1.0

    rates = fx.get(base)
    if not rates:
        return {}

    converted: dict[date, float] = {}
    rate_days = sorted(rates)

    for day, price in series.items():
        rate = rates.get(day)
        if rate is None:
            # Valutakurser mangler på helligdager der børsen var åpen. Bruk nærmeste
            # foregående kurs framfor å kaste dagen.
            earlier = [d for d in rate_days if d <= day]
            if not earlier:
                continue
            rate = rates[earlier[-1]]
        converted[day] = price / divisor * rate

    return converted


def _stats(series: dict[date, float], window_start: date) -> dict[str, Any] | None:
    """Regn ut avkastning, årlig snitt og største fall for en kursserie i NOK."""
    days = sorted(series)
    if len(days) < MIN_ROWS:
        return None

    first, last = days[0], days[-1]
    start_price, end_price = series[first], series[last]

    # Serien er allerede renset, men en beregning som gir NaN skal aldri nå rapporten.
    if not (start_price > 0 and end_price > 0):
        return None

    years = (last - first).days / 365.25
    total = (end_price / start_price - 1.0) * 100.0
    cagr = ((end_price / start_price) ** (1.0 / years) - 1.0) * 100.0 if years > 0.5 else None

    peak = start_price
    drawdown = 0.0
    for day in days:
        price = series[day]
        peak = max(peak, price)
        drawdown = min(drawdown, price / peak - 1.0)

    return {
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
        "years_covered": round(years, 2),
        "covers_full_window": first <= window_start + timedelta(days=14),
        "total_return_pct": round(total, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "max_drawdown_pct": round(drawdown * 100.0, 2),
    }


async def _resolve(
    name: str, label: str, candidates: tuple[str, ...], start: date, end: date,
    fx: dict[str, dict[date, float]],
) -> dict[str, Any]:
    """Prøv kandidat-tickerne i tur og orden til én gir brukbar historikk."""
    for ticker in candidates:
        series = await _history(ticker, start, end)

        if len(series) < MIN_ROWS:
            continue

        currency = await _currency(ticker) or "USD"
        in_nok = _to_nok(series, currency, fx)
        stats = _stats(in_nok, start)

        if stats is None:
            continue

        logger.info("%s: brukte %s (%s), %d dager", name, ticker, currency, len(series))
        return {"key": name, "label": label, "ticker": ticker, "currency": currency, **stats}

    logger.warning("%s: ingen av tickerne ga historikk (%s)", name, ", ".join(candidates))
    return {"key": name, "label": label, "ticker": None, "error": "ingen historikk"}


async def main() -> int:
    """Hent alt og skriv JSON til stdout eller fil."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=LOOKBACK_DAYS)

    fx: dict[str, dict[date, float]] = {}
    for currency, symbol in FX_SYMBOLS.items():
        fx[currency] = await _history(symbol, start, end)
        logger.info("valuta %s: %d dager", currency, len(fx[currency]))

    if not fx.get("USD"):
        logger.error("uten USDNOK kan ingenting regnes om til kroner")
        return 1

    funds = [await _resolve(k, lbl, t, start, end, fx) for k, lbl, t in FUNDS]
    marks = [await _resolve(k, lbl, t, start, end, fx) for k, lbl, t in BENCHMARKS]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "measured_in": "NOK",
        "note": (
            "Avkastning er regnet i kroner: fondets kursserie er konvertert med "
            "daglige valutakurser, slik en norsk investor faktisk opplever den. "
            "covers_full_window er false for fond som ble startet inne i perioden."
        ),
        "funds": funds,
        "benchmarks": marks,
    }

    out = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(out, encoding="utf-8")
        logger.info("skrev %s", sys.argv[1])
    else:
        sys.stdout.write(out)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
