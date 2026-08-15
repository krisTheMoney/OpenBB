"""
Revaluering av posisjoner, avkastning og dekomponering av valutaeffekt.

Modulen er ren regning uten nettverkskall, slik at den kan testes offline. Alt som
henter data ligger i sources.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import PortfolioConfig, Position


@dataclass(frozen=True)
class FxRates:
    """USDNOK på de tidspunktene revalueringen trenger."""

    now: float
    previous_close: float
    snapshot: float

    @property
    def day_change_pct(self) -> float:
        """Endring i USDNOK siden forrige close, i prosent."""
        if self.previous_close == 0:
            return 0.0
        return (self.now / self.previous_close - 1.0) * 100.0


@dataclass(frozen=True)
class Quote:
    """Siste kurs og forrige close for én ticker, i notert valuta."""

    symbol: str
    price: float
    previous_close: float

    @property
    def day_change_pct(self) -> float:
        """Kursendring siden forrige close, i prosent og uten valutaeffekt."""
        if self.previous_close == 0:
            return 0.0
        return (self.price / self.previous_close - 1.0) * 100.0


@dataclass(frozen=True)
class ValuedPosition:
    """En posisjon revaluert mot dagens kurs og dagens USDNOK."""

    symbol: str
    name: str
    shares: float
    shares_derived: bool
    price: float
    previous_close: float
    market_value_nok: float
    cost_basis_nok: float
    unrealized_pl_nok: float
    unrealized_pl_pct: float
    day_change_pct: float
    day_change_nok: float
    stock_effect_nok: float
    fx_effect_nok: float
    weight_pct: float


@dataclass(frozen=True)
class UnavailablePosition:
    """En posisjon det ikke fantes kursdata for i denne kjøringen."""

    symbol: str
    name: str
    reason: str


@dataclass(frozen=True)
class PortfolioValuation:
    """Hele porteføljen revaluert, med kontanter og valutaeffekt."""

    positions: tuple[ValuedPosition, ...]
    unavailable: tuple[UnavailablePosition, ...]
    cash_nok: float
    fx: FxRates
    total_value_nok: float
    total_cost_basis_nok: float
    total_unrealized_pl_nok: float
    total_unrealized_pl_pct: float
    day_change_nok: float
    day_change_pct: float
    stock_effect_nok: float
    fx_effect_nok: float

    @property
    def by_day_contribution(self) -> tuple[ValuedPosition, ...]:
        """Posisjonene sortert etter dagens bidrag i kroner, størst først."""
        return tuple(
            sorted(self.positions, key=lambda p: p.day_change_nok, reverse=True)
        )


def derive_shares(position: Position, usdnok_snapshot: float) -> tuple[float, bool]:
    """
    Finn antall aksjer for en posisjon.

    Megler-appen viser fraksjonelle poster i NOK, ikke antall aksjer. Antallet utledes
    derfor fra avlesningen: verdien i NOK delt på kursen i USD ganget med USDNOK samme
    dag. Er 'shares' satt eksplisitt i configen, brukes den i stedet.

    Returns
    -------
    tuple[float, bool]
        Antall aksjer, og om tallet ble utledet (True) eller lest fra configen (False).
    """
    if position.shares is not None:
        return position.shares, False

    if usdnok_snapshot <= 0:
        raise ValueError(
            f"kan ikke utlede antall aksjer for {position.symbol}: "
            f"ugyldig USDNOK på avlesningsdagen ({usdnok_snapshot})"
        )

    shares = position.snapshot_value_nok / (position.snapshot_price * usdnok_snapshot)
    return shares, True


def value_position(
    position: Position,
    quote: Quote,
    fx: FxRates,
    shares: float,
    shares_derived: bool,
    total_value_nok: float,
) -> ValuedPosition:
    """
    Revaluer én posisjon og del dagens endring i aksje- og valutabidrag.

    Dekomponeringen bruker denne konvensjonen, som går nøyaktig opp uten restledd:
    aksjebidraget verdsettes til gårsdagens USDNOK, valutabidraget til dagens kurs.
    """
    market_value_nok = shares * quote.price * fx.now
    unrealized_pl_nok = market_value_nok - position.cost_basis_nok
    unrealized_pl_pct = (
        unrealized_pl_nok / position.cost_basis_nok * 100.0
        if position.cost_basis_nok
        else 0.0
    )

    stock_effect_nok = shares * (quote.price - quote.previous_close) * fx.previous_close
    fx_effect_nok = shares * quote.price * (fx.now - fx.previous_close)

    return ValuedPosition(
        symbol=position.symbol,
        name=position.name,
        shares=shares,
        shares_derived=shares_derived,
        price=quote.price,
        previous_close=quote.previous_close,
        market_value_nok=market_value_nok,
        cost_basis_nok=position.cost_basis_nok,
        unrealized_pl_nok=unrealized_pl_nok,
        unrealized_pl_pct=unrealized_pl_pct,
        day_change_pct=quote.day_change_pct,
        day_change_nok=stock_effect_nok + fx_effect_nok,
        stock_effect_nok=stock_effect_nok,
        fx_effect_nok=fx_effect_nok,
        weight_pct=(
            market_value_nok / total_value_nok * 100.0 if total_value_nok else 0.0
        ),
    )


def value_portfolio(
    config: PortfolioConfig,
    quotes: dict[str, Quote],
    fx: FxRates,
) -> PortfolioValuation:
    """
    Revaluer hele porteføljen.

    Posisjoner uten kursdata utelates fra summene og rapporteres separat, slik at én
    ticker som feiler ikke velter hele oppdateringen.
    """
    resolved: list[tuple[Position, Quote, float, bool]] = []
    unavailable: list[UnavailablePosition] = []

    for position in config.positions:
        quote = quotes.get(position.symbol)

        if quote is None:
            unavailable.append(
                UnavailablePosition(
                    symbol=position.symbol,
                    name=position.name,
                    reason="ingen kursdata fra kilden",
                )
            )
            continue

        try:
            shares, derived = derive_shares(position, fx.snapshot)
        except ValueError as error:
            unavailable.append(
                UnavailablePosition(
                    symbol=position.symbol, name=position.name, reason=str(error)
                )
            )
            continue

        resolved.append((position, quote, shares, derived))

    positions_value_nok = sum(
        shares * quote.price * fx.now for _, quote, shares, _ in resolved
    )
    total_value_nok = positions_value_nok + config.cash_nok

    valued = tuple(
        value_position(position, quote, fx, shares, derived, total_value_nok)
        for position, quote, shares, derived in resolved
    )

    total_cost_basis_nok = sum(p.cost_basis_nok for p in valued)
    total_unrealized_pl_nok = sum(p.unrealized_pl_nok for p in valued)
    stock_effect_nok = sum(p.stock_effect_nok for p in valued)
    fx_effect_nok = sum(p.fx_effect_nok for p in valued)
    day_change_nok = stock_effect_nok + fx_effect_nok

    # Gårsdagens verdi, brukt som nevner for dagens prosentendring. Kontanter er med
    # fordi prosenten skal beskrive porteføljen slik brukeren ser den i appen.
    previous_total_nok = total_value_nok - day_change_nok

    return PortfolioValuation(
        positions=valued,
        unavailable=tuple(unavailable),
        cash_nok=config.cash_nok,
        fx=fx,
        total_value_nok=total_value_nok,
        total_cost_basis_nok=total_cost_basis_nok,
        total_unrealized_pl_nok=total_unrealized_pl_nok,
        total_unrealized_pl_pct=(
            total_unrealized_pl_nok / total_cost_basis_nok * 100.0
            if total_cost_basis_nok
            else 0.0
        ),
        day_change_nok=day_change_nok,
        day_change_pct=(
            day_change_nok / previous_total_nok * 100.0 if previous_total_nok else 0.0
        ),
        stock_effect_nok=stock_effect_nok,
        fx_effect_nok=fx_effect_nok,
    )
