"""Små hjelpefunksjoner som flere moduler deler."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def coerce_date(value: Any) -> date | None:
    """
    Gjør en dato fra en provider om til en date.

    Providerne returnerer dato som date, datetime eller streng avhengig av endepunkt,
    så alle tre må håndteres.
    """
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
