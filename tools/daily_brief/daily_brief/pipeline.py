"""Kobler sammen henting, revaluering og rendering til én oppdatering."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import PortfolioConfig
from .events import build_events
from .market import build_market_snapshot
from .news import build_news
from .positions import FxRates, value_portfolio
from .render import Brief
from .sources import FetchResult, collect


class BriefError(RuntimeError):
    """Oppdateringen kunne ikke bygges."""


def build_brief(
    config: PortfolioConfig,
    result: FetchResult,
    fx: FxRates | None,
    now: datetime | None = None,
) -> Brief:
    """
    Sett sammen en ferdig oppdatering av config og hentede data.

    USDNOK er den ene harde avhengigheten: hele porteføljen er notert i USD, så uten
    kursen finnes det ingen kronetall å rapportere. Da stopper vi med en tydelig feil
    heller enn å publisere en rapport som ser komplett ut men mangler poenget.
    """
    generated_at = now or datetime.now(timezone.utc)

    if fx is None:
        raise BriefError(
            "USDNOK mangler, og uten den kan ikke porteføljen verdsettes i kroner. "
            "Se loggen for hvilken kilde som feilet."
        )

    valuation = value_portfolio(config, result.quotes, fx)
    market = build_market_snapshot(result)

    return Brief(
        generated_at=generated_at,
        trading_day=market.as_of,
        config=config,
        valuation=valuation,
        market=market,
        events=build_events(result, config.symbols, generated_at.date()),
        news=build_news(result, generated_at),
        issues=tuple(result.issues),
    )


async def run_pipeline(
    config: PortfolioConfig, now: datetime | None = None
) -> Brief:
    """Hent alle data og bygg oppdateringen."""
    generated_at = now or datetime.now(timezone.utc)
    result, fx = await collect(
        config.symbols,
        config.snapshot_date,
        generated_at.date(),
        config.snapshot_usdnok,
    )
    return build_brief(config, result, fx, generated_at)
