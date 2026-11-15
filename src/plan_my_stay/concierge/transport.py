"""Transport: the Concierge proposes the fastest mode for each trip. Plain code, no LLM (docs/PLAN.md)."""

import datetime as dt
from collections.abc import Mapping
from typing import Any

from plan_my_stay.enums import Mode
from plan_my_stay.models import Leg

# On a tie the calmer mode wins: walking, then transit, then driving.
_TIE_ORDER: tuple[Mode, ...] = (Mode.WALKING, Mode.TRANSIT, Mode.DRIVING)


def fastest_mode(minutes_by_mode: Mapping[Any, float | None]) -> Mode | None:
    """The mode with the smallest travel time, ignoring modes with no route. None when no mode has a route."""
    best: Mode | None = None
    best_minutes = 0.0
    for mode in _TIE_ORDER:
        minutes = minutes_by_mode.get(mode)
        if minutes is None:
            continue
        if best is None or minutes < best_minutes:  # strict: an earlier mode in the tie order keeps a tie
            best, best_minutes = mode, float(minutes)
    return best


def build_leg(
    *,
    from_id: str,
    to_id: str,
    depart_at: dt.datetime,
    minutes_by_mode: Mapping[Any, float | None],
) -> Leg:
    """A leg with Routing's times and the fastest mode chosen. `chosen_mode` is None when Routing found no route."""
    return Leg(
        from_id=from_id,
        to_id=to_id,
        depart_at=depart_at,
        minutes_by_mode={Mode(mode): minutes for mode, minutes in minutes_by_mode.items()},
        chosen_mode=fastest_mode(minutes_by_mode),
    )
