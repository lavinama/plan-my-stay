"""Choosing the fastest mode per leg: plain code, no LLM."""

import datetime as dt

import pytest
from pydantic import ValidationError

from plan_my_stay.concierge.transport import build_leg, fastest_mode
from plan_my_stay.enums import Mode
from plan_my_stay.timeutil import TRIP_TZ

DEPART = dt.datetime(2026, 11, 17, 12, 0, tzinfo=TRIP_TZ)


def test_fastest_mode_is_the_one_with_the_fewest_minutes():
    assert fastest_mode({Mode.WALKING: 20, Mode.TRANSIT: 12, Mode.DRIVING: 15}) is Mode.TRANSIT


def test_fastest_mode_ignores_modes_without_a_route():
    assert fastest_mode({Mode.WALKING: None, Mode.TRANSIT: 30, Mode.DRIVING: 25}) is Mode.DRIVING


def test_fastest_mode_is_none_when_no_mode_has_minutes():
    assert fastest_mode({}) is None
    assert fastest_mode({Mode.WALKING: None, Mode.TRANSIT: None}) is None


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        ({Mode.WALKING: 10, Mode.TRANSIT: 10, Mode.DRIVING: 10}, Mode.WALKING),
        ({Mode.TRANSIT: 10, Mode.DRIVING: 10}, Mode.TRANSIT),
        ({Mode.WALKING: 12, Mode.TRANSIT: 10, Mode.DRIVING: 10}, Mode.TRANSIT),
    ],
)
def test_ties_prefer_walking_then_transit_then_driving(minutes, expected):
    assert fastest_mode(minutes) is expected


def test_fastest_mode_accepts_plain_string_keys_from_json():
    assert fastest_mode({"driving": 5, "transit": 9}) is Mode.DRIVING


def test_fastest_mode_does_not_change_its_input():
    minutes = {Mode.WALKING: 9.0, Mode.TRANSIT: None}
    fastest_mode(minutes)
    assert minutes == {Mode.WALKING: 9.0, Mode.TRANSIT: None}


def test_build_leg_sets_the_chosen_mode():
    leg = build_leg(
        from_id="B", to_id="stay", depart_at=DEPART, minutes_by_mode={Mode.WALKING: 21, Mode.TRANSIT: 12, Mode.DRIVING: 9}
    )

    assert (leg.from_id, leg.to_id, leg.depart_at) == ("B", "stay", DEPART)
    assert leg.chosen_mode is Mode.DRIVING
    assert leg.minutes_by_mode[Mode.TRANSIT] == 12


def test_build_leg_with_no_route_has_no_chosen_mode():
    leg = build_leg(from_id="A", to_id="B", depart_at=DEPART, minutes_by_mode={Mode.WALKING: None})

    assert leg.chosen_mode is None


def test_build_leg_rejects_a_naive_departure_time():
    with pytest.raises(ValidationError):
        build_leg(from_id="A", to_id="B", depart_at=dt.datetime(2026, 11, 17, 12, 0), minutes_by_mode={Mode.WALKING: 5})


def test_build_leg_rejects_negative_minutes():
    with pytest.raises(ValidationError):
        build_leg(from_id="A", to_id="B", depart_at=DEPART, minutes_by_mode={Mode.WALKING: -1})
