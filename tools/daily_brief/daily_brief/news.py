"""Filtrering av selskapsnyheter til det som faktisk er ferskt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .sources import FetchResult

# Mandagsrapporten skal fange nyheter fra hele helgen, så vinduet er romsligere enn
# ett døgn. Rapporten oppgir alltid alderen på hver sak, så leseren ser hva som er nytt.
DEFAULT_WINDOW_HOURS = 36
MAX_ITEMS_PER_SYMBOL = 4


@dataclass(frozen=True)
class NewsItem:
    """En nyhetssak knyttet til én posisjon."""

    symbol: str
    title: str
    url: str
    published: datetime
    source: str | None

    def age_hours(self, now: datetime) -> float:
        """Hvor mange timer siden saken ble publisert."""
        return (now - self.published).total_seconds() / 3600.0


def _parse_published(value: Any) -> datetime | None:
    """Tolk publiseringstidspunktet og normaliser til UTC."""
    if value is None:
        return None

    parsed = value

    if not isinstance(parsed, datetime):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def build_news(
    result: FetchResult,
    now: datetime | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> dict[str, tuple[NewsItem, ...]]:
    """
    Plukk ut ferske, unike nyhetssaker per ticker.

    Duplikater fjernes på URL, siden samme sak ofte kommer fra flere kilder.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(hours=window_hours)
    selected: dict[str, tuple[NewsItem, ...]] = {}

    for symbol, records in result.news.items():
        seen_urls: set[str] = set()
        items: list[NewsItem] = []

        for record in records:
            published = _parse_published(record.get("date"))
            url = str(record.get("url") or "").strip()
            title = str(record.get("title") or "").strip()

            if published is None or published < cutoff or not title:
                continue

            if url and url in seen_urls:
                continue

            seen_urls.add(url)
            items.append(
                NewsItem(
                    symbol=symbol,
                    title=title,
                    url=url,
                    published=published,
                    source=record.get("source"),
                )
            )

        if items:
            items.sort(key=lambda item: item.published, reverse=True)
            selected[symbol] = tuple(items[:MAX_ITEMS_PER_SYMBOL])

    return selected
