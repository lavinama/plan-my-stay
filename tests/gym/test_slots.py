"""The exact demo numbers from docs/PLAN.md must fall out of this algorithm.

"The 07:00 workout the user prefers is impossible on Tuesday, because
check-in is Tuesday, so ... the Gym agent posts a workout after meeting B,
from about 17:30." Wednesday has no check-in constraint, so 07:00 itself
should be used.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from workers.gym.slots import (
    TRIP_TZ,
    TimeRange,
    day_window,
    eligible_gaps,
    free_gaps,
    is_checkin_day,
    location_mode,
    pick_time,
)

NY = ZoneInfo("America/New_York")


def dt(day, hour, minute):
    return datetime(2026, 11, day, hour, minute, tzinfo=NY)


CHECK_IN = "2026-11-17"
WEDNESDAY = "2026-11-18"


def test_tuesday_slot_is_17_30_to_18_15():
    window = day_window(CHECK_IN, [])
    meetings = [
        TimeRange(dt(17, 11, 0), dt(17, 12, 0)),  # meeting A
        TimeRange(dt(17, 16, 0), dt(17, 17, 0)),  # meeting B
    ]
    gaps = free_gaps(window, meetings, buffer_minutes=30)
    eligible = eligible_gaps(gaps, CHECK_IN, CHECK_IN)
    slot = pick_time(eligible, time(7, 0), session_minutes=45)

    assert slot is not None
    assert slot.start == dt(17, 17, 30)
    assert slot.end == dt(17, 18, 15)


def test_wednesday_slot_is_07_00_to_07_45():
    window = day_window(WEDNESDAY, [])
    meetings = [TimeRange(dt(18, 10, 0), dt(18, 11, 0))]  # meeting C
    gaps = free_gaps(window, meetings, buffer_minutes=30)
    eligible = eligible_gaps(gaps, WEDNESDAY, CHECK_IN)
    slot = pick_time(eligible, time(7, 0), session_minutes=45)

    assert slot is not None
    assert slot.start == dt(18, 7, 0)
    assert slot.end == dt(18, 7, 45)


def test_location_mode_is_near_meeting_on_checkin_day_regardless_of_tier():
    # Tier 2 fired for Tuesday above; location_mode must still be
    # near_meeting purely because it's the check-in date.
    assert location_mode(CHECK_IN, CHECK_IN) == "near_meeting"


def test_location_mode_is_near_stay_on_any_other_day():
    assert location_mode(WEDNESDAY, CHECK_IN) == "near_stay"


def test_pick_time_returns_none_when_every_gap_is_too_short():
    window = TimeRange(dt(17, 9, 0), dt(17, 9, 40))
    gaps = [window]
    assert pick_time(gaps, time(7, 0), session_minutes=45) is None


def test_checkin_day_exclusion_only_drops_the_pre_first_meeting_gap():
    # Three meetings on the check-in day; only the gap before the first
    # meeting should be dropped, not the gap between the later two.
    window = day_window(CHECK_IN, [])
    meetings = [
        TimeRange(dt(17, 9, 0), dt(17, 10, 0)),
        TimeRange(dt(17, 12, 0), dt(17, 13, 0)),
        TimeRange(dt(17, 16, 0), dt(17, 17, 0)),
    ]
    gaps = free_gaps(window, meetings, buffer_minutes=30)
    eligible = eligible_gaps(gaps, CHECK_IN, CHECK_IN)

    # free_gaps produces 4 gaps: pre-9am, 10-12, 13-16, post-17.
    assert len(gaps) == 4
    # eligible_gaps drops only the first (pre-9am) one.
    assert len(eligible) == 3
    assert eligible == gaps[1:]


def test_is_checkin_day():
    assert is_checkin_day(CHECK_IN, CHECK_IN) is True
    assert is_checkin_day(WEDNESDAY, CHECK_IN) is False
