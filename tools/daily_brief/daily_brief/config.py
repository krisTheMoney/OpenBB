"""Innlesing og validering av portfolio.toml."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

# Hvor mye summen av posisjoner pluss kontanter får avvike fra tallet megleren viste.
# Avrunding i appen gjør at et lite avvik er normalt; alt over dette er en feillesing.
TOTAL_TOLERANCE_NOK = 1.0


class ConfigError(ValueError):
    """Portefølje-configen er ugyldig eller inkonsistent."""


@dataclass(frozen=True)
class Position:
    """En enkelt posisjon slik den er ført i portfolio.toml."""

    symbol: str
    name: str
    cost_basis_nok: float
    snapshot_price: float
    snapshot_value_nok: float
    shares: float | None = None

    @property
    def snapshot_pl_nok(self) -> float:
        """Urealisert gevinst eller tap i NOK på avlesningstidspunktet."""
        return self.snapshot_value_nok - self.cost_basis_nok


@dataclass(frozen=True)
class PortfolioConfig:
    """Hele porteføljen, lest fra portfolio.toml."""

    base_currency: str
    reported_total_nok: float
    snapshot_date: date
    snapshot_note: str
    cash_nok: float
    positions: tuple[Position, ...]
    snapshot_usdnok: float | None = None

    @property
    def symbols(self) -> list[str]:
        """Tickerne i porteføljen, i den rekkefølgen de står i configen."""
        return [position.symbol for position in self.positions]

    @property
    def snapshot_positions_nok(self) -> float:
        """Samlet posisjonsverdi på avlesningstidspunktet, uten kontanter."""
        return sum(position.snapshot_value_nok for position in self.positions)

    @property
    def snapshot_total_nok(self) -> float:
        """Samlet porteføljeverdi på avlesningstidspunktet, kontanter inkludert."""
        return self.snapshot_positions_nok + self.cash_nok

    @property
    def total_cost_basis_nok(self) -> float:
        """Samlet kostpris for alle posisjoner."""
        return sum(position.cost_basis_nok for position in self.positions)


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    """Hent en påkrevd nøkkel, eller si tydelig hva som mangler."""
    if key not in mapping:
        raise ConfigError(f"'{key}' mangler i {context}")
    return mapping[key]


def _parse_position(raw: dict[str, Any], index: int) -> Position:
    """Gjør én [[positions]]-tabell om til en Position."""
    context = f"posisjon nr. {index + 1}"
    symbol = str(_require(raw, "symbol", context)).strip().upper()

    if not symbol:
        raise ConfigError(f"tom 'symbol' i {context}")

    position = Position(
        symbol=symbol,
        name=str(raw.get("name", symbol)),
        cost_basis_nok=float(_require(raw, "cost_basis_nok", context)),
        snapshot_price=float(_require(raw, "snapshot_price", context)),
        snapshot_value_nok=float(_require(raw, "snapshot_value_nok", context)),
        shares=float(raw["shares"]) if raw.get("shares") is not None else None,
    )

    if position.snapshot_price <= 0:
        raise ConfigError(f"'snapshot_price' må være positiv for {symbol}")

    if position.snapshot_value_nok <= 0:
        raise ConfigError(f"'snapshot_value_nok' må være positiv for {symbol}")

    if position.shares is not None and position.shares <= 0:
        raise ConfigError(f"'shares' må være positiv for {symbol}")

    return position


def load_portfolio(path: str | Path) -> PortfolioConfig:
    """
    Les og valider portfolio.toml.

    Parameters
    ----------
    path
        Sti til TOML-filen.

    Returns
    -------
    PortfolioConfig
        Ferdig validert portefølje.
    """
    config_path = Path(path)

    if not config_path.is_file():
        raise ConfigError(f"fant ikke portefølje-config: {config_path}")

    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    meta = raw.get("meta", {})
    raw_positions = raw.get("positions", [])

    if not raw_positions:
        raise ConfigError("configen inneholder ingen posisjoner")

    positions = tuple(
        _parse_position(entry, index) for index, entry in enumerate(raw_positions)
    )

    duplicates = {
        symbol
        for symbol in (position.symbol for position in positions)
        if [p.symbol for p in positions].count(symbol) > 1
    }
    if duplicates:
        raise ConfigError(f"samme ticker står flere ganger: {', '.join(sorted(duplicates))}")

    snapshot_date = _require(meta, "snapshot_date", "[meta]")
    if not isinstance(snapshot_date, date):
        snapshot_date = date.fromisoformat(str(snapshot_date))

    snapshot_usdnok = meta.get("snapshot_usdnok")

    if snapshot_usdnok is not None:
        snapshot_usdnok = float(snapshot_usdnok)
        if snapshot_usdnok <= 0:
            raise ConfigError("'snapshot_usdnok' må være positiv")

    return PortfolioConfig(
        base_currency=str(meta.get("base_currency", "NOK")).upper(),
        reported_total_nok=float(_require(meta, "reported_total_nok", "[meta]")),
        snapshot_date=snapshot_date,
        snapshot_note=str(meta.get("snapshot_note", "")),
        cash_nok=float(raw.get("cash", {}).get("nok", 0.0)),
        positions=positions,
        snapshot_usdnok=snapshot_usdnok,
    )


def validate_totals(config: PortfolioConfig) -> tuple[bool, str]:
    """
    Sjekk at posisjoner pluss kontanter stemmer med totalen megleren viste.

    Dette fanger feillesing av et skjermbilde før tallene rekker å havne i en rapport.

    Returns
    -------
    tuple[bool, str]
        Om summen stemmer, og en forklarende melding uansett utfall.
    """
    computed = config.snapshot_total_nok
    difference = computed - config.reported_total_nok

    summary = (
        f"posisjoner {config.snapshot_positions_nok:,.2f} "
        f"+ kontanter {config.cash_nok:,.2f} "
        f"= {computed:,.2f} mot oppgitt {config.reported_total_nok:,.2f} "
        f"(avvik {difference:+,.2f} NOK)"
    )

    if abs(difference) <= TOTAL_TOLERANCE_NOK:
        return True, f"Summen stemmer: {summary}"

    return False, (
        f"Summen stemmer ikke: {summary}. "
        f"Toleransen er {TOTAL_TOLERANCE_NOK:.2f} NOK. "
        "Kontroller avlesningen eller oppdater reported_total_nok."
    )
