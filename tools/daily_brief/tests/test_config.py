"""Tester for innlesing og validering av portfolio.toml."""

from datetime import date

import pytest
from daily_brief.config import ConfigError, load_portfolio, validate_totals

VALID_TOML = """
[meta]
reported_total_nok = 1100.0
snapshot_date = "2026-08-14"

[cash]
nok = 100.0

[[positions]]
symbol = "aapl"
name = "Apple"
cost_basis_nok = 900.0
snapshot_price = 200.0
snapshot_value_nok = 1000.0
"""


def _write(tmp_path, content):
    """Skriv en config til disk og returner stien."""
    path = tmp_path / "portfolio.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_real_portfolio(config):
    """Den faktiske porteføljen leses inn med alle åtte posisjonene."""
    assert len(config.positions) == 8
    assert config.symbols == ["KO", "JPM", "TSLA", "GOOG", "META", "MP", "CEG", "SOFI"]
    assert config.cash_nok == pytest.approx(2498.96)
    assert config.snapshot_date == date(2026, 8, 14)


def test_real_portfolio_totals_match(config):
    """Posisjoner pluss kontanter stemmer med totalen megleren viste."""
    is_valid, message = validate_totals(config)
    assert is_valid, message


def test_cost_basis_matches_reported_pl(config):
    """Kostbasis er utledet slik at samlet urealisert P/L blir som i appen."""
    # Verdi minus kostbasis på avlesningstidspunktet, summert over porteføljen.
    # Fasit er summen av P/L-tallene megler-appen viste for de åtte posisjonene.
    total_pl = sum(position.snapshot_pl_nok for position in config.positions)
    assert total_pl == pytest.approx(5014.02, abs=0.01)


def test_snapshot_usdnok_is_read(config):
    """Den kalibrerte valutakursen leses fra configen."""
    # Kalibrert mot meglerens rapporterte totalverdi, ikke Yahoos close på 9,5042.
    assert config.snapshot_usdnok == pytest.approx(9.439780)


def test_snapshot_usdnok_is_optional(tmp_path):
    """Uten den i configen faller vi tilbake på kursen kilden gir."""
    config = load_portfolio(_write(tmp_path, VALID_TOML))
    assert config.snapshot_usdnok is None


def test_negative_snapshot_usdnok_is_rejected(tmp_path):
    """En ugyldig valutakurs ville skalert hele porteføljen feil."""
    broken = VALID_TOML.replace(
        'snapshot_date = "2026-08-14"',
        'snapshot_date = "2026-08-14"\nsnapshot_usdnok = -1.0',
    )

    with pytest.raises(ConfigError, match="snapshot_usdnok"):
        load_portfolio(_write(tmp_path, broken))


def test_symbols_are_upper_cased(tmp_path):
    """Tickere normaliseres til store bokstaver."""
    config = load_portfolio(_write(tmp_path, VALID_TOML))
    assert config.symbols == ["AAPL"]


def test_missing_file_is_reported(tmp_path):
    """En config som ikke finnes gir en tydelig feil."""
    with pytest.raises(ConfigError, match="fant ikke"):
        load_portfolio(tmp_path / "nope.toml")


def test_missing_field_is_reported(tmp_path):
    """En posisjon uten kostbasis avvises."""
    broken = VALID_TOML.replace("cost_basis_nok = 900.0\n", "")

    with pytest.raises(ConfigError, match="cost_basis_nok"):
        load_portfolio(_write(tmp_path, broken))


def test_duplicate_symbols_are_rejected(tmp_path):
    """Samme ticker to ganger er nesten alltid en feillesing."""
    doubled = VALID_TOML + """
[[positions]]
symbol = "AAPL"
cost_basis_nok = 10.0
snapshot_price = 200.0
snapshot_value_nok = 10.0
"""

    with pytest.raises(ConfigError, match="flere ganger"):
        load_portfolio(_write(tmp_path, doubled))


def test_no_positions_is_rejected(tmp_path):
    """En tom portefølje gir ingen mening å rapportere på."""
    empty = """
[meta]
reported_total_nok = 0.0
snapshot_date = "2026-08-14"
"""

    with pytest.raises(ConfigError, match="ingen posisjoner"):
        load_portfolio(_write(tmp_path, empty))


def test_totals_mismatch_is_caught(tmp_path):
    """Feillesing av et skjermbilde fanges før det havner i en rapport."""
    wrong = VALID_TOML.replace("reported_total_nok = 1100.0", "reported_total_nok = 9999.0")
    config = load_portfolio(_write(tmp_path, wrong))

    is_valid, message = validate_totals(config)

    assert not is_valid
    assert "stemmer ikke" in message
    assert "8,899.00" in message


def test_small_rounding_difference_is_accepted(tmp_path):
    """Ett øre fra eller til er avrunding i appen, ikke en feil."""
    rounded = VALID_TOML.replace("reported_total_nok = 1100.0", "reported_total_nok = 1100.4")
    config = load_portfolio(_write(tmp_path, rounded))

    is_valid, _ = validate_totals(config)

    assert is_valid
