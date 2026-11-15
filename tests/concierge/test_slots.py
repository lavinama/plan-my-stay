"""Slots for stays of any length (the sibling suite checks the basic shape; these pin the preferences and edge cases)."""

import datetime as dt

from plan_my_stay.concierge.slots import build_slots, workout_window

D17, D19, D20 = dt.date(2026, 11, 17), dt.date(2026, 11, 19), dt.date(2026, 11, 20)


def test_a_longer_stay_gets_slots_for_every_day_before_check_out():
    slots = build_slots(D17, D20, wants_workouts=True)

    assert [s.date.day for s in slots if s.kind == "dinner"] == [17, 18, 19]
    assert [s.id for s in slots] == [f"s{n}" for n in range(1, 10)]


def test_the_preferred_workout_time_sets_the_later_mornings_window():
    slots = build_slots(D17, D19, wants_workouts=True, preferred_workout_start="06:30")
    morning = [s for s in slots if s.kind == "workout"][1]

    assert (morning.window.start, morning.window.end) == ("06:30", "08:00")


def test_the_check_in_day_workout_ignores_a_morning_preference():
    first = [s for s in build_slots(D17, D19, preferred_workout_start="06:30") if s.kind == "workout"][0]

    assert first.window.start == "17:00"


def test_a_very_late_preference_falls_back_to_the_default_morning_window():
    assert (workout_window("23:59").start, workout_window("23:59").end) == ("07:00", "08:30")


def test_the_window_never_runs_past_midnight():
    window = workout_window("23:00")

    assert window.end == "23:59" and window.start < window.end
