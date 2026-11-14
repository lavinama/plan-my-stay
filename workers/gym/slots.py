"""Pure gap-finding and time/location picking for the Gym agent.

No I/O here: everything takes and returns datetimes, dataclasses and plain
values, so the hard algorithm (which is what has to reproduce the exact
demo numbers in docs/PLAN.md) can be unit-tested without fixtures, state or
the message envelope.

docs/PLAN.md's demo script: "The 07:00 workout the user prefers is impossible
on Tuesday, because check-in is Tuesday, so ... the Gym agent posts a workout
after meeting B, from about 17:30." Wednesday has no check-in constraint, so
the user's preferred 07:00 is free and should be used directly. This module
is what has to reproduce 17:30-18:15 and 07:00-07:45 exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

TRIP_TZ = ZoneInfo("America/New_York")

DEFAULT_DAY_START = time(6, 0)
DEFAULT_DAY_END = time(22, 0)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def _parse_date(date: str) -> date_cls:
    return date_cls.fromisoformat(date)


def day_window(date: str, gym_slots: list[dict]) -> TimeRange:
    """The hours to search for a workout on `date`.

    If the request carries an explicit slot of kind "gym" for this date, use
    its window verbatim (the Concierge or the user may have narrowed it).
    Otherwise default to 06:00-22:00 America/New_York, which is the window
    docs/PLAN.md's own gap math (06:00-22:00 Tuesday and Wednesday) assumes.
    """
    for slot in gym_slots:
        if slot.get("kind") == "gym" and slot.get("date") == date:
            window = slot["window"]
            day = _parse_date(date)
            start = datetime.combine(
                day, time.fromisoformat(window["start"]), tzinfo=TRIP_TZ
            )
            end = datetime.combine(
                day, time.fromisoformat(window["end"]), tzinfo=TRIP_TZ
            )
            return TimeRange(start=start, end=end)

    day = _parse_date(date)
    return TimeRange(
        start=datetime.combine(day, DEFAULT_DAY_START, tzinfo=TRIP_TZ),
        end=datetime.combine(day, DEFAULT_DAY_END, tzinfo=TRIP_TZ),
    )


def free_gaps(
    window: TimeRange, meetings: list[TimeRange], buffer_minutes: int = 30
) -> list[TimeRange]:
    """The open intervals in `window` once `meetings` are blocked out.

    Meetings are sorted and merged if overlapping or touching. Each
    remaining gap is then shrunk inward by `buffer_minutes`, but only on the
    side(s) that border an actual meeting -- the window's own open and close
    boundaries are not meetings and are left alone. A gap that becomes zero
    or negative length after shrinking is dropped. Gaps are returned in
    chronological order.

    Worked example (docs/PLAN.md's Tuesday: window 06:00-22:00, meetings
    11:00-12:00 and 16:00-17:00, buffer 30m):
      before meeting A: 06:00-11:00 -> shrink right (touches A)  -> 06:00-10:30
      between A and B:  12:00-16:00 -> shrink both sides          -> 12:30-15:30
      after meeting B:  17:00-22:00 -> shrink left (touches B)    -> 17:30-22:00
    """
    if not meetings:
        return [window] if window.end > window.start else []

    buffer = timedelta(minutes=buffer_minutes)
    merged: list[TimeRange] = []
    for meeting in sorted(meetings, key=lambda m: m.start):
        if merged and meeting.start <= merged[-1].end:
            merged[-1] = TimeRange(
                start=merged[-1].start, end=max(merged[-1].end, meeting.end)
            )
        else:
            merged.append(meeting)

    gaps: list[TimeRange] = []
    cursor = window.start
    for index, meeting in enumerate(merged):
        gap_start = cursor
        if index > 0:
            # Left side borders the previous meeting's end.
            gap_start = gap_start + buffer
        # Right side always borders this meeting.
        gap_end = meeting.start - buffer
        if gap_end > gap_start:
            gaps.append(TimeRange(start=gap_start, end=gap_end))
        cursor = meeting.end

    # Trailing gap after the last meeting, up to the window's close. Its
    # left side borders a meeting; its right side borders the window's own
    # close, not a meeting, so only the left side is shrunk.
    trailing_start = cursor + buffer
    if window.end > trailing_start:
        gaps.append(TimeRange(start=trailing_start, end=window.end))

    return gaps


def is_checkin_day(date: str, check_in: str) -> bool:
    return date == check_in


def eligible_gaps(
    gaps: list[TimeRange], date: str, check_in: str
) -> list[TimeRange]:
    """Drop the pre-arrival gap on the check-in day; keep all others as-is.

    docs/PLAN.md line 37: "Assuming the traveler arrives on Tuesday and goes
    straight to A, A doesn't constrain the stay" -- the same reasoning rules
    out a workout before the day's first meeting on check-in day, because
    the traveler is still arriving, not already free. Any other day has no
    such constraint, so every gap stays eligible, including gaps between
    later meetings.
    """
    if not gaps or not is_checkin_day(date, check_in):
        return list(gaps)
    return list(gaps[1:])


def pick_time(
    gaps: list[TimeRange], preferred: time, session_minutes: int = 45
) -> TimeRange | None:
    """Pick one workout slot from the eligible gaps.

    Tier 1: the user's preferred start time, if a gap actually contains the
      full [preferred, preferred + session_minutes] interval.
    Tier 2: otherwise, the trailing gap (the last eligible gap, open toward
      the window's close with no meeting after it), starting at its own
      open, if it's long enough.
    Tier 3: otherwise, the longest remaining gap, clipped to session_minutes
      at its start, if any gap is long enough; else None.
    """
    if not gaps:
        return None

    session = timedelta(minutes=session_minutes)

    for gap in gaps:
        preferred_dt = datetime.combine(gap.start.date(), preferred, tzinfo=gap.start.tzinfo)
        if gap.start <= preferred_dt and preferred_dt + session <= gap.end:
            return TimeRange(start=preferred_dt, end=preferred_dt + session)

    trailing = gaps[-1]
    if trailing.duration >= session:
        return TimeRange(start=trailing.start, end=trailing.start + session)

    longest = max(gaps, key=lambda g: g.duration)
    if longest.duration >= session:
        return TimeRange(start=longest.start, end=longest.start + session)

    return None


def location_mode(date: str, check_in: str) -> Literal["near_meeting", "near_stay"]:
    """Where to search for a gym: near a meeting on check-in day, else the stay.

    Independent of which tier picked the time -- even a Tier 2/3 slot on the
    check-in day still searches near the bordering meeting, because the
    traveler has no stay location yet that day.
    """
    return "near_meeting" if is_checkin_day(date, check_in) else "near_stay"
