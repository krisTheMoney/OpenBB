"""Tester for revaluering, avkastning og dekomponering av valutaeffekt."""

import pytest
from daily_brief.config import Position
from daily_brief.positions import FxRates, Quote, derive_shares, value_portfolio, value_position

FX = FxRates(now=10.05, previous_close=10.02, snapshot=10.0)


def _position(**overrides):
    """Bygg en posisjon med fornuftige standardverdier."""
    defaults = {
        "symbol": "TEST",
        "name": "Testselskap",
        "cost_basis_nok": 1000.0,
        "snapshot_price": 100.0,
        "snapshot_value_nok": 2000.0,
    }
    defaults.update(overrides)
    return Position(**defaults)


def test_shares_derived_from_snapshot():
    """Antall aksjer utledes av verdi i NOK delt på kurs ganger USDNOK."""
    shares, derived = derive_shares(_position(), usdnok_snapshot=10.0)

    # 2000 NOK / (100 USD * 10 NOK/USD) = 2 aksjer
    assert shares == pytest.approx(2.0)
    assert derived is True


def test_explicit_shares_win_over_derivation():
    """Er antallet oppgitt i configen, brukes det uendret."""
    shares, derived = derive_shares(_position(shares=7.5), usdnok_snapshot=10.0)

    assert shares == pytest.approx(7.5)
    assert derived is False


def test_derivation_requires_valid_fx():
    """Uten en gyldig USDNOK er antallet umulig å utlede."""
    with pytest.raises(ValueError, match="ugyldig USDNOK"):
        derive_shares(_position(), usdnok_snapshot=0.0)


def test_real_portfolio_shares(config, bundle):
    """De faktiske posisjonene gir de forventede fraksjonelle antallene."""
    _, fx = bundle
    shares = {
        position.symbol: derive_shares(position, fx.snapshot)[0]
        for position in config.positions
    }

    # 21 173,50 NOK / (87,71 USD * 10,0) = 24,1404 aksjer
    assert shares["KO"] == pytest.approx(24.1404, abs=0.0001)
    assert shares["SOFI"] == pytest.approx(34.2831, abs=0.0001)
    assert all(value > 0 for value in shares.values())


def test_decomposition_is_exact():
    """Aksjebidrag pluss valutabidrag er nøyaktig lik total endring, uten restledd."""
    quote = Quote(symbol="TEST", price=110.0, previous_close=100.0)
    valued = value_position(_position(), quote, FX, shares=2.0, shares_derived=True, total_value_nok=2211.0)

    expected_total = 2.0 * (110.0 * FX.now - 100.0 * FX.previous_close)

    assert valued.stock_effect_nok + valued.fx_effect_nok == pytest.approx(expected_total)
    assert valued.day_change_nok == pytest.approx(expected_total)


def test_pure_fx_move_has_no_stock_effect():
    """Står kursen stille, skyldes hele endringen valuta."""
    quote = Quote(symbol="TEST", price=100.0, previous_close=100.0)
    valued = value_position(_position(), quote, FX, shares=2.0, shares_derived=True, total_value_nok=2010.0)

    assert valued.stock_effect_nok == pytest.approx(0.0)
    assert valued.fx_effect_nok == pytest.approx(2.0 * 100.0 * (10.05 - 10.02))


def test_pure_stock_move_has_no_fx_effect():
    """Står valutaen stille, skyldes hele endringen aksjen."""
    flat_fx = FxRates(now=10.0, previous_close=10.0, snapshot=10.0)
    quote = Quote(symbol="TEST", price=110.0, previous_close=100.0)
    valued = value_position(_position(), quote, flat_fx, shares=2.0, shares_derived=True, total_value_nok=2200.0)

    assert valued.fx_effect_nok == pytest.approx(0.0)
    assert valued.stock_effect_nok == pytest.approx(2.0 * 10.0 * 10.0)


def test_unrealized_return_uses_cost_basis():
    """Avkastningen måles mot kostbasis i kroner."""
    quote = Quote(symbol="TEST", price=100.0, previous_close=100.0)
    valued = value_position(_position(), quote, FX, shares=2.0, shares_derived=True, total_value_nok=2010.0)

    # 2 aksjer * 100 USD * 10,05 = 2010 NOK mot 1000 NOK kostbasis
    assert valued.market_value_nok == pytest.approx(2010.0)
    assert valued.unrealized_pl_nok == pytest.approx(1010.0)
    assert valued.unrealized_pl_pct == pytest.approx(101.0)


def test_portfolio_totals_add_up(config, bundle):
    """Summene i porteføljen stemmer med posisjonene de er bygget av."""
    result, fx = bundle
    valuation = value_portfolio(config, result.quotes, fx)

    assert len(valuation.positions) == 8
    assert valuation.unavailable == ()

    positions_value = sum(p.market_value_nok for p in valuation.positions)
    assert valuation.total_value_nok == pytest.approx(positions_value + config.cash_nok)

    assert valuation.day_change_nok == pytest.approx(
        valuation.stock_effect_nok + valuation.fx_effect_nok
    )
    assert valuation.total_unrealized_pl_nok == pytest.approx(
        sum(p.unrealized_pl_nok for p in valuation.positions)
    )


def test_weights_sum_to_share_of_portfolio(config, bundle):
    """Vektene summerer til porteføljen minus kontantandelen."""
    result, fx = bundle
    valuation = value_portfolio(config, result.quotes, fx)

    cash_weight = config.cash_nok / valuation.total_value_nok * 100.0
    total_weight = sum(p.weight_pct for p in valuation.positions)

    assert total_weight + cash_weight == pytest.approx(100.0)


def test_missing_quote_is_isolated(config, bundle):
    """En ticker uten kursdata tas ut av summene, resten rapporteres som normalt."""
    result, fx = bundle
    del result.quotes["TSLA"]

    valuation = value_portfolio(config, result.quotes, fx)

    assert len(valuation.positions) == 7
    assert [item.symbol for item in valuation.unavailable] == ["TSLA"]
    assert all(p.symbol != "TSLA" for p in valuation.positions)


def test_positions_sorted_by_day_contribution(config, bundle):
    """Sorteringen setter dagens største bidragsyter først."""
    result, fx = bundle
    valuation = value_portfolio(config, result.quotes, fx)

    contributions = [p.day_change_nok for p in valuation.by_day_contribution]

    assert contributions == sorted(contributions, reverse=True)
    # TSLA steg 2,67 prosent på en posisjon på nitten tusen og bidrar mest i kroner,
    # foran MP som steg mer i prosent men fra en mindre posisjon.
    assert valuation.by_day_contribution[0].symbol == "TSLA"
    assert valuation.by_day_contribution[1].symbol == "MP"
