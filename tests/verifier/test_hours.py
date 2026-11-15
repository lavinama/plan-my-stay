"""is_open_during: opening hours against a slot, including split days and periods that run past midnight."""

import datetime as dt

import pytest

from plan_my_stay.verifier.hours import is_open_during

MON = dt.date(2026, 11, 16)
TUE = dt.date(2026, 11, 17)
WED = dt.date(2026, 11, 18)
SUN = dt.date(2026, 11, 22)


def test_inside_a_single_period_is_open():
    assert is_open_during({"Tue": "11:00-22:00"}, TUE, "18:30", "19:45") is True


def test_the_edges_of_the_period_count_as_open():
    hours = {"Tue": "11:00-22:00"}

    assert is_open_during(hours, TUE, "11:00", "12:00") is True
    assert is_open_during(hours, TUE, "21:00", "22:00") is True


def test_closing_before_the_slot_ends_is_closed():
    # The demo narrative: the restaurant closes at 19:30 but dinner runs to 19:45.
    assert is_open_during({"Tue": "11:00-19:30"}, TUE, "18:30", "19:45") is False


def test_opening_after_the_slot_starts_is_closed():
    assert is_open_during({"Tue": "19:00-23:00"}, TUE, "18:30", "19:45") is False


def test_a_slot_wholly_outside_the_period_is_closed():
    assert is_open_during({"Tue": "06:00-09:00"}, TUE, "18:30", "19:45") is False


def test_missing_hours_cannot_be_told():
    assert is_open_during(None, TUE, "18:30", "19:45") is None


def test_no_entry_for_that_weekday_cannot_be_told():
    assert is_open_during({"Wed": "11:00-22:00"}, TUE, "18:30", "19:45") is None


def test_an_empty_hours_map_cannot_be_told():
    assert is_open_during({}, TUE, "18:30", "19:45") is None


def test_the_weekday_comes_from_the_date():
    hours = {"Mon": "09:00-10:00", "Tue": "18:00-22:00"}

    assert is_open_during(hours, TUE, "18:30", "19:45") is True
    assert is_open_during(hours, MON, "18:30", "19:45") is False
    assert is_open_during({"Sun": "10:00-14:00"}, SUN, "11:00", "12:00") is True


def test_split_hours_match_either_period():
    hours = {"Tue": ("11:30-14:30", "17:30-22:00")}

    assert is_open_during(hours, TUE, "12:00", "13:00") is True
    assert is_open_during(hours, TUE, "18:30", "19:45") is True


def test_split_hours_closed_in_the_break():
    hours = {"Tue": ("11:30-14:30", "17:30-22:00")}

    assert is_open_during(hours, TUE, "15:00", "16:00") is False


def test_a_slot_spanning_the_break_is_closed():
    hours = {"Tue": ("11:30-14:30", "17:30-22:00")}

    assert is_open_during(hours, TUE, "14:00", "18:00") is False


def test_touching_periods_count_as_one_open_stretch():
    hours = {"Tue": ("10:00-14:00", "14:00-18:00")}

    assert is_open_during(hours, TUE, "13:00", "15:00") is True


def test_a_period_past_midnight_is_open_late_on_its_own_day():
    hours = {"Mon": "18:00-02:00"}

    assert is_open_during(hours, MON, "23:00", "23:59") is True
    assert is_open_during(hours, MON, "21:00", "23:30") is True


def test_a_period_past_midnight_spills_into_the_next_weekday():
    hours = {"Mon": "18:00-02:00", "Tue": "11:00-22:00"}

    assert is_open_during(hours, TUE, "00:00", "02:00") is True
    assert is_open_during(hours, TUE, "00:30", "01:30") is True


def test_the_spill_stops_at_the_closing_time():
    hours = {"Mon": "18:00-02:00", "Tue": "11:00-22:00"}

    assert is_open_during(hours, TUE, "01:00", "03:00") is False
    assert is_open_during(hours, TUE, "02:00", "03:00") is False


def test_the_spill_joins_the_next_days_own_period():
    hours = {"Mon": "18:00-02:00", "Tue": "00:00-12:00"}

    assert is_open_during(hours, TUE, "01:00", "04:00") is True


def test_a_spill_alone_answers_for_a_day_with_no_entry_when_the_slot_fits_inside_it():
    hours = {"Mon": "18:00-02:00"}  # nothing for Tuesday

    assert is_open_during(hours, TUE, "00:30", "01:30") is True


def test_a_day_with_no_entry_stays_unknown_beyond_the_spill():
    hours = {"Mon": "18:00-02:00"}

    assert is_open_during(hours, TUE, "10:00", "11:00") is None
    assert is_open_during(hours, TUE, "01:00", "03:00") is None  # half covered, the rest unknown


def test_sunday_night_spills_into_monday():
    hours = {"Sun": "20:00-01:00"}

    assert is_open_during(hours, dt.date(2026, 11, 23), "00:15", "00:45") is True  # Sunday 22 Nov spills into Monday 23 Nov


def test_the_spill_does_not_reach_a_second_day():
    hours = {"Mon": "18:00-02:00", "Tue": "11:00-12:00", "Wed": "11:00-12:00"}

    assert is_open_during(hours, WED, "00:15", "00:45") is False


def test_an_end_equal_to_the_start_runs_a_full_day():
    hours = {"Tue": "09:00-09:00"}

    assert is_open_during(hours, TUE, "09:00", "23:00") is True
    assert is_open_during(hours, TUE, "08:00", "09:00") is False


def test_a_listed_day_with_no_periods_is_closed():
    assert is_open_during({"Tue": ()}, TUE, "18:30", "19:45") is False


def test_an_unreadable_period_does_not_hide_a_good_one():
    hours = {"Tue": ("closed", "17:30-22:00")}

    assert is_open_during(hours, TUE, "18:30", "19:45") is True


def test_an_unreadable_period_makes_a_miss_unknown_rather_than_closed():
    hours = {"Tue": ("closed", "17:30-19:00")}

    assert is_open_during(hours, TUE, "18:30", "19:45") is None


def test_an_unreadable_single_period_cannot_be_told():
    assert is_open_during({"Tue": "all day"}, TUE, "18:30", "19:45") is None


def test_a_period_that_is_not_a_real_clock_time_is_unreadable():
    assert is_open_during({"Tue": "25:00-26:00"}, TUE, "18:30", "19:45") is None


def test_an_unreadable_period_the_night_before_makes_a_miss_unknown():
    # Monday's entry could have spilled into Tuesday morning; we cannot say it did not.
    hours = {"Mon": "all night", "Tue": "11:00-22:00"}

    assert is_open_during(hours, TUE, "18:30", "19:45") is True
    assert is_open_during(hours, TUE, "02:00", "03:00") is None


@pytest.mark.parametrize(
    ("start", "end"),
    [("6:30", "7:00"), ("18:30", "25:00"), ("18:30", "18:30"), ("19:00", "18:00"), ("", "18:00")],
)
def test_a_bad_slot_is_a_programming_error(start, end):
    with pytest.raises(ValueError):
        is_open_during({"Tue": "11:00-22:00"}, TUE, start, end)
