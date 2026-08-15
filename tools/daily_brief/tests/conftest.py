"""Felles oppsett for testene."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from daily_brief.config import load_portfolio  # noqa: E402
from daily_brief.offline import load_bundle  # noqa: E402
from daily_brief.pipeline import build_brief  # noqa: E402

FIXTURE = PACKAGE_ROOT / "tests" / "fixtures" / "offline_bundle.json"
CONFIG = PACKAGE_ROOT / "portfolio.toml"

# Fast tidspunkt, slik at relative datoer i bundelen gir samme resultat hver kjøring.
REFERENCE_NOW = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def config():
    """Den faktiske porteføljen fra portfolio.toml."""
    return load_portfolio(CONFIG)


@pytest.fixture
def bundle():
    """Lagrede API-svar og valutakurser."""
    return load_bundle(FIXTURE, REFERENCE_NOW)


@pytest.fixture
def brief(config, bundle):
    """En ferdig bygget oppdatering basert på testdataene."""
    result, fx = bundle
    return build_brief(config, result, fx, REFERENCE_NOW)
