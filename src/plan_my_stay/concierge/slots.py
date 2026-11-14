"""The meal and workout windows the specialists fill, for a stay of any length.

One lunch, one dinner and (if wanted) one workout for each day of the stay, from the check-in day through the
day before check-out. The New York demo uses `demo.demo_slots()` instead, so its slot ids match the plan's example.
"""

import datetime as dt

from plan_my_stay.models import Slot, Window

_LUNCH = Window(start="11:30", end="14:30")
_DINNER = Window(start="17:30", end="21:00")
_CHECK_IN_DAY_WORKOUT = Window(
    start="17:00", end="20:00"
)  # the traveler arrives that day: the workout is after the meetings
_MORNING_WORKOUT = Window(start="07:00", end="08:30")
_WORKOUT_MINUTES = 90


def workout_window(preferred_start: str | None) -> Window:
    """A 90-minute window starting at the preferred clock time, or the default morning window."""
    if preferred_start is None:
        return _MORNING_WORKOUT
    start = dt.time.fromisoformat(preferred_start)
    end_minutes = min(start.hour * 60 + start.minute + _WORKOUT_MINUTES, 23 * 60 + 59)
    if end_minutes <= start.hour * 60 + start.minute:
        return _MORNING_WORKOUT  # no room after a start this late
    return Window(start=preferred_start, end=f"{end_minutes // 60:02d}:{end_minutes % 60:02d}")


def build_slots(
    check_in: dt.date,
    check_out: dt.date,
    *,
    wants_workouts: bool = True,
    preferred_workout_start: str | None = None,
) -> tuple[Slot, ...]:
    """Slots `s1`, `s2`, ... in day order (lunch, dinner, workout). No workout slots when `wants_workouts` is false."""
    slots: list[Slot] = []

    def add(kind: str, day: dt.date, window: Window) -> None:
        slots.append(Slot(id=f"s{len(slots) + 1}", kind=kind, date=day, window=window))

    for offset in range((check_out - check_in).days):
        day = check_in + dt.timedelta(days=offset)
        add("lunch", day, _LUNCH)
        add("dinner", day, _DINNER)
        if wants_workouts:
            add("workout", day, _CHECK_IN_DAY_WORKOUT if offset == 0 else workout_window(preferred_workout_start))
    return tuple(slots)
