"""Static zones, the generic slot builder, and the demo intake."""

import datetime as dt

import pytest

from plan_my_stay import demo
from plan_my_stay.concierge.intake import MeetingIntake, TripIntake, parse_trip_request
from plan_my_stay.concierge.slots import build_slots
from plan_my_stay.concierge.zones import StaticZones, widen_zones
from plan_my_stay.models import Anchor
from plan_my_stay.timeutil import TRIP_TZ


# --- zones ------------------------------------------------------------------------------------------------------------


def test_new_york_gets_the_demo_zones():
    zones = StaticZones().zones_for("New York, NY", demo.demo_anchors())

    assert [z.id for z in zones] == ["z1", "z2", "z3"]
    assert zones == demo.demo_zones()


def test_new_york_is_recognised_however_it_is_written():
    assert StaticZones().zones_for("new york", demo.demo_anchors()) == demo.demo_zones()


def test_another_city_gets_one_zone_around_the_anchors_with_a_margin():
    anchors = (
        Anchor(id="A", lat=51.50, lng=-0.12, start=demo.at(17, 9), end=demo.at(17, 10), counts_for_stay=True),
        Anchor(id="B", lat=51.52, lng=-0.10, start=demo.at(18, 9), end=demo.at(18, 10), counts_for_stay=True),
    )
    (zone,) = StaticZones(margin_degrees=0.01).zones_for("London", anchors)

    assert zone.id == "z1"
    assert zone.bounds.sw.lat == pytest.approx(51.49)
    assert zone.bounds.ne.lat == pytest.approx(51.53)
    assert zone.bounds.sw.lng == pytest.approx(-0.13)
    assert zone.bounds.ne.lng == pytest.approx(-0.09)


def test_zone_bounds_stay_on_the_map():
    anchor = Anchor(id="A", lat=89.999, lng=179.999, start=demo.at(17, 9), end=demo.at(17, 10), counts_for_stay=True)

    (zone,) = StaticZones(margin_degrees=0.5).zones_for("Somewhere", (anchor,))

    assert zone.bounds.ne.lat <= 90
    assert zone.bounds.ne.lng <= 180


def test_a_city_with_no_anchors_gets_no_zones_rather_than_a_made_up_one():
    assert StaticZones().zones_for("London", ()) == ()


def test_widen_zones_grows_every_box_and_leaves_the_input_alone():
    original = demo.demo_zones()
    wider = widen_zones(original, margin_degrees=0.02)

    assert wider[0].bounds.ne.lat == pytest.approx(original[0].bounds.ne.lat + 0.02)
    assert wider[0].bounds.sw.lng == pytest.approx(original[0].bounds.sw.lng - 0.02)
    assert original == demo.demo_zones()
    assert [z.id for z in wider] == [z.id for z in original]


# --- slots ------------------------------------------------------------------------------------------------------------


def test_slots_cover_each_night_s_day_with_lunch_dinner_and_a_workout():
    slots = build_slots(dt.date(2026, 11, 17), dt.date(2026, 11, 19), wants_workouts=True)

    assert [(s.date.day, s.kind) for s in slots] == [
        (17, "lunch"),
        (17, "dinner"),
        (17, "workout"),
        (18, "lunch"),
        (18, "dinner"),
        (18, "workout"),
    ]
    assert len({s.id for s in slots}) == len(slots)


def test_no_workout_slots_when_the_user_skips_workouts():
    slots = build_slots(dt.date(2026, 11, 17), dt.date(2026, 11, 19), wants_workouts=False)

    assert "workout" not in {s.kind for s in slots}


def test_the_workout_on_check_in_day_is_in_the_evening_and_later_ones_in_the_morning():
    slots = build_slots(dt.date(2026, 11, 17), dt.date(2026, 11, 19), wants_workouts=True)
    workouts = [s for s in slots if s.kind == "workout"]

    assert workouts[0].window.start >= "17:00"  # the traveler arrives that day
    assert workouts[1].window.start < "09:00"


# --- intake -----------------------------------------------------------------------------------------------------------


def test_the_first_pass_returns_the_new_york_demo_trip():
    intake = parse_trip_request("Plan my trip to New York, I have three meetings", None)

    assert isinstance(intake, TripIntake)
    assert intake.city == "New York, NY"
    assert (intake.check_in, intake.check_out) == (dt.date(2026, 11, 17), dt.date(2026, 11, 19))
    assert intake.budget.amount == 1000
    assert intake.guests == 1
    assert intake.wants_workouts is True
    assert [m.id for m in intake.meetings] == ["A", "B", "C"]
    assert {m.id: m.address for m in intake.meetings} == demo.MEETING_ADDRESSES
    assert intake.meetings[0].start == dt.datetime(2026, 11, 17, 11, 0, tzinfo=TRIP_TZ)
    assert intake.slots == demo.demo_slots()


def test_the_word_demo_gives_the_same_trip():
    assert parse_trip_request("run the demo", None) == parse_trip_request("anything else", None)


def test_the_llm_parameter_is_accepted_but_not_used_yet():
    class Boom:
        def complete(self, **_: object) -> str:
            raise AssertionError("LLM intake is deferred")

    assert parse_trip_request("plan a trip", Boom()).city == "New York, NY"


@pytest.mark.parametrize(
    "text",
    [
        "demo trip, no workouts please",
        "demo trip, skip the gym",
        "demo trip without a workout",
        "demo trip - I don't want workouts",
    ],
)
def test_a_request_to_skip_workouts_turns_them_off(text):
    intake = parse_trip_request(text, None)

    assert intake.wants_workouts is False
    assert "workout" not in {s.kind for s in intake.slots}


def test_asking_for_a_workout_keeps_workouts_on():
    assert parse_trip_request("demo trip with a morning workout", None).wants_workouts is True


def test_a_meeting_must_end_after_it_starts():
    with pytest.raises(ValueError):
        MeetingIntake(id="A", address="1 Main St", start=demo.at(17, 12), end=demo.at(17, 11))
