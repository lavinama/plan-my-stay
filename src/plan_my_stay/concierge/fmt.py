"""Small formatting helpers shared by the itinerary, the gate's notes and the on-screen messages."""

import datetime as dt

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def fmt_money(amount: float, currency: str = "USD") -> str:
    """`$1,000.00`, `€35.00`, or `CHF 35.00` for a currency with no symbol here."""
    symbol = _SYMBOLS.get(currency)
    figure = f"{amount:,.2f}"
    return f"{symbol}{figure}" if symbol else f"{currency} {figure}"


def fmt_day(day: dt.date) -> str:
    """`Tue 17 Nov` (locale-independent)."""
    return f"{day:%a} {day.day} {day:%b}"


def fmt_clock(moment: dt.datetime) -> str:
    return f"{moment:%H:%M}"


def fmt_minutes(minutes: float) -> str:
    """`25 min`, `4.5 min`."""
    return f"{minutes:g} min"


def fmt_distance(metres: float) -> str:
    """`150 m`, `1.2 km`."""
    return f"{round(metres)} m" if metres < 1000 else f"{metres / 1000:.1f} km"
