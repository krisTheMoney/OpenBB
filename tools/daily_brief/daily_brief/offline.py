"""
Innlesing av lagrede API-svar, brukt av --offline og av testene.

Miljøet oppdateringen utvikles i når ikke Yahoo Finance, så den eneste måten å teste
rendering og regning på er mot lagrede svar. Bundelen inneholder rådata i samme form
som providerne returnerer, slik at hele kjeden etter selve nettverkskallet kjøres.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .positions import FxRates, Quote
from .sources import FetchResult


def _resolve_relative_dates(
    records: list[dict[str, Any]], reference: datetime
) -> list[dict[str, Any]]:
    """
    Gjør relative datofelter i bundelen om til faktiske datoer.

    Nyheter og resultatdatoer må ligge nær kjøretidspunktet for at filtreringen skal
    oppføre seg som i produksjon. Bundelen oppgir dem derfor som 'hours_ago' og
    'days_ahead' i stedet for faste datoer som ville blitt gamle.
    """
    resolved: list[dict[str, Any]] = []

    for record in records:
        entry = dict(record)

        if "hours_ago" in entry:
            entry["date"] = (
                reference - timedelta(hours=float(entry.pop("hours_ago")))
            ).isoformat()

        if "days_ahead" in entry:
            entry["report_date"] = (
                reference.date() + timedelta(days=int(entry.pop("days_ahead")))
            ).isoformat()

        if "days_ago" in entry:
            entry["ex_dividend_date"] = (
                reference.date() - timedelta(days=int(entry.pop("days_ago")))
            ).isoformat()

        resolved.append(entry)

    return resolved


def load_bundle(
    path: str | Path, now: datetime | None = None
) -> tuple[FetchResult, FxRates]:
    """
    Bygg et FetchResult og valutakurser fra en JSON-bundel.

    Parameters
    ----------
    path
        Sti til bundelen.
    now
        Referansetidspunkt for relative datoer. Standard er nå i UTC.

    Returns
    -------
    tuple[FetchResult, FxRates]
        Samme typer som de ekte kildene produserer.
    """
    bundle_path = Path(path)
    reference = now or datetime.now(timezone.utc)

    with bundle_path.open(encoding="utf-8") as handle:
        bundle: dict[str, Any] = json.load(handle)

    result = FetchResult()

    for symbol, quote in bundle.get("quotes", {}).items():
        result.quotes[symbol.upper()] = Quote(
            symbol=symbol.upper(),
            price=float(quote["price"]),
            previous_close=float(quote["previous_close"]),
        )

    for symbol, series in bundle.get("series", {}).items():
        result.series[symbol] = (
            float(series["last"]),
            float(series["previous"]),
            date.fromisoformat(series["as_of"]),
        )

    result.news = {
        symbol: _resolve_relative_dates(records, reference)
        for symbol, records in bundle.get("news", {}).items()
    }
    result.earnings = _resolve_relative_dates(list(bundle.get("earnings", [])), reference)
    result.dividends = {
        symbol: _resolve_relative_dates(records, reference)
        for symbol, records in bundle.get("dividends", {}).items()
    }
    result.targets = dict(bundle.get("targets", {}))

    fx_data = bundle["fx"]
    fx = FxRates(
        now=float(fx_data["now"]),
        previous_close=float(fx_data["previous_close"]),
        snapshot=float(fx_data["snapshot"]),
    )

    return result, fx
