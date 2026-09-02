"""Markedsoversikten: amerikanske indekser, renter, valuta og råvarer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .sources import MARKET_INSTRUMENTS, FetchResult


@dataclass(frozen=True)
class MarketMove:
    """Ett instrument med siste nivå og bevegelsen siden forrige close."""

    symbol: str
    label: str
    category: str
    last: float
    previous: float
    as_of: date

    @property
    def change(self) -> float:
        """Endring i instrumentets egen enhet."""
        return self.last - self.previous

    @property
    def change_pct(self) -> float:
        """Endring i prosent."""
        if self.previous == 0:
            return 0.0
        return (self.last / self.previous - 1.0) * 100.0

    @property
    def change_bp(self) -> float:
        """Endring i basispunkter. Meningsfull kun for renter."""
        return self.change * 100.0


@dataclass(frozen=True)
class MarketSnapshot:
    """Hele markedsoversikten, gruppert etter type instrument."""

    moves: tuple[MarketMove, ...]

    def by_category(self, category: str) -> tuple[MarketMove, ...]:
        """Instrumentene i én kategori, i den rekkefølgen de er definert."""
        return tuple(move for move in self.moves if move.category == category)

    @property
    def as_of(self) -> date | None:
        """Siste handelsdag oversikten bygger på."""
        if not self.moves:
            return None
        return max(move.as_of for move in self.moves)


def build_market_snapshot(result: FetchResult) -> MarketSnapshot:
    """Sett sammen markedsoversikten fra hentede serier, og hopp over det som mangler."""
    moves: list[MarketMove] = []

    for symbol, label, category in MARKET_INSTRUMENTS:
        series = result.series.get(symbol)

        if series is None:
            continue

        last, previous, as_of = series
        moves.append(
            MarketMove(
                symbol=symbol,
                label=label,
                category=category,
                last=last,
                previous=previous,
                as_of=as_of,
            )
        )

    return MarketSnapshot(moves=tuple(moves))
