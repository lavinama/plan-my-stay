"""Opening-hours arithmetic: is a place open for the whole of a time slot on a given date?

Shared by the Verifier and by the Restaurant and Gym agents, so it stays small and pure.

Input shape (`plan_my_stay.models.Hours`): weekday keys `Mon`..`Sun`, each a period
`"HH:MM-HH:MM"` or a tuple of periods for split hours. A period whose end is not after its
start runs past midnight and spills into the next weekday: `Mon "18:00-02:00"` is open
Monday 18:00 to 24:00 and Tuesday 00:00 to 02:00. An end equal to the start means open for
the full 24 hours from that start.

Answers are three-valued, because the Verifier must not call a place closed when the data
simply isn't there: `True` (open for the whole slot), `False` (the data says it is not) and
`None` (cannot tell).
"""

import datetime as dt
import logging
import re
from collections.abc import Mapping

from plan_my_stay.models import Hours

logger = logging.getLogger(__name__)

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MINUTES_PER_DAY = 24 * 60
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_PERIOD = re.compile(r"^(\d{2}:\d{2})-(\d{2}:\d{2})$")

Interval = tuple[int, int]  # minutes from midnight, start inclusive, end inclusive


def clock_minutes(clock: str) -> int:
    """Minutes after midnight for a 24-hour `HH:MM`. Raises ValueError for anything else."""
    match = _CLOCK.match(clock)
    if match is None:
        raise ValueError(f"not a 24-hour HH:MM time: {clock!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def weekday_key(day: dt.date) -> str:
    """`Mon`..`Sun`, the key the hours maps use."""
    return _WEEKDAYS[day.weekday()]


def _periods(value: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value)


def _split(period: str) -> tuple[Interval, int | None] | None:
    """One period as (the part on its own day, the part spilling into the next day), or None if unreadable."""
    match = _PERIOD.match(period.strip()) if isinstance(period, str) else None
    if match is None:
        return None
    try:
        start, end = clock_minutes(match.group(1)), clock_minutes(match.group(2))
    except ValueError:
        return None
    if end > start:
        return (start, end), None
    return (start, _MINUTES_PER_DAY), end  # runs past midnight: the rest is open from 00:00 to `end` next day


def _own_intervals(entry: str | tuple[str, ...], unreadable: list[str]) -> list[Interval]:
    intervals: list[Interval] = []
    for period in _periods(entry):
        split = _split(period)
        if split is None:
            unreadable.append(str(period))
        else:
            intervals.append(split[0])
    return intervals


def _spill_intervals(entry: str | tuple[str, ...], unreadable: list[str]) -> list[Interval]:
    """What the previous weekday's entry leaves open from 00:00 on the day after it."""
    intervals: list[Interval] = []
    for period in _periods(entry):
        split = _split(period)
        if split is None:
            unreadable.append(str(period))
        elif split[1] is not None and split[1] > 0:
            intervals.append((0, split[1]))
    return intervals


def _merge(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def is_open_during(hours: Hours | Mapping[str, object] | None, day: dt.date, start: str, end: str) -> bool | None:
    """Whether `hours` keep the place open for all of `start`-`end` (24-hour `HH:MM`) on `day`.

    - `True`: one open stretch covers the whole slot (touching the opening or closing minute counts).
      The previous weekday's past-midnight spill counts, even when this weekday has no entry.
    - `False`: the hours for that day are known and do not cover it.
    - `None`: cannot tell: `hours` is missing or empty, the weekday has no entry (and no spill covers
      the slot), or an unreadable period might have covered it.

    A listed day with no periods (`()`) means closed. The slot must end after it starts and stay on
    one day; a malformed slot raises `ValueError`, because that is a bug in the caller, not missing data.
    """
    slot_start, slot_end = clock_minutes(start), clock_minutes(end)
    if slot_end <= slot_start:
        raise ValueError(f"slot must end after it starts: {start}-{end}")
    if not hours:
        return None

    unreadable: list[str] = []
    own_entry = hours.get(weekday_key(day))
    previous_entry = hours.get(weekday_key(day - dt.timedelta(days=1)))

    intervals: list[Interval] = []
    if own_entry is not None:
        intervals += _own_intervals(own_entry, unreadable)
    if previous_entry is not None:
        intervals += _spill_intervals(previous_entry, unreadable)

    if any(lo <= slot_start and slot_end <= hi for lo, hi in _merge(intervals)):
        return True
    if unreadable:
        logger.warning("ignoring unreadable opening period(s) %s on %s", unreadable, weekday_key(day))
        return None
    return None if own_entry is None else False
