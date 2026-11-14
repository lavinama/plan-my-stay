"""Intake: the user's request becomes a TripIntake (LLM structured output), with the demo trip as the fallback."""

import datetime as dt
import json

import pytest

from plan_my_stay import demo
from plan_my_stay.concierge.intake import MeetingIntake, TripIntake, demo_intake, fell_back, parse_trip_request
from plan_my_stay.concierge.llm import FakeLLM, LLMError

TODAY = dt.date(2026, 11, 14)

GOOD = {
    "city": "Boston, MA",
    "check_in": "2026-12-01",
    "check_out": "2026-12-03",
    "budget_amount": 800,
    "currency": "USD",
    "guests": 2,
    "wants_workouts": False,
    "meetings": [
        {"address": "1 Beacon St, Boston, MA", "date": "2026-12-02", "start": "14:00", "end": "15:00"},
        {"address": "10 Post Office Sq, Boston, MA", "date": "2026-12-01", "start": "10:00", "end": "11:00"},
    ],
}


def test_the_demo_trip_matches_the_plan():
    intake = demo_intake()

    assert intake.is_demo and intake.city == demo.CITY
    assert (intake.check_in, intake.check_out) == (dt.date(2026, 11, 17), dt.date(2026, 11, 19))
    assert intake.budget.amount == 1000 and intake.wants_workouts
    assert intake.slots == demo.demo_slots()
    assert [m.id for m in intake.meetings] == ["A", "B", "C"]
    assert {m.id: m.address for m in intake.meetings} == demo.MEETING_ADDRESSES
    assert [(m.start, m.end) for m in intake.meetings] == [(a.start, a.end) for a in demo.demo_anchors()]


@pytest.mark.parametrize("text", ["plan the demo trip", "DEMO", "Demo, please. Thanks!"])
def test_the_word_demo_returns_the_demo_trip_without_calling_the_llm(text):
    llm = FakeLLM([])

    assert parse_trip_request(text, llm).is_demo
    assert llm.calls == ()


def test_the_word_demo_must_be_a_whole_word():
    llm = FakeLLM([json.dumps(GOOD)])
    intake = parse_trip_request("I need to demonstrate a product in Boston", llm, today=TODAY)

    assert not intake.is_demo and intake.city == "Boston, MA"


def test_demo_trip_can_skip_the_gym():
    assert not parse_trip_request("demo trip, no workouts", None).wants_workouts
    assert not parse_trip_request("demo, skip the gym", None).wants_workouts
    assert parse_trip_request("demo trip", None).wants_workouts


def test_no_llm_falls_back_to_the_demo_trip_and_says_so():
    text = "a trip to Boston next week"
    intake = parse_trip_request(text, None)

    assert intake.is_demo and fell_back(text, intake)
    assert not fell_back(
        "plan the demo", parse_trip_request("plan the demo", None)
    )  # asked for the demo: not a fallback


def test_the_llm_reads_the_request_into_an_intake_with_meetings_in_time_order():
    llm = FakeLLM([json.dumps(GOOD)])
    intake = parse_trip_request("Boston Dec 1 to 3, two of us, $800, no gym. Meetings: ...", llm, today=TODAY)

    assert not intake.is_demo
    assert (intake.city, intake.guests, intake.budget.amount, intake.wants_workouts) == ("Boston, MA", 2, 800, False)
    assert (intake.check_in, intake.check_out) == (dt.date(2026, 12, 1), dt.date(2026, 12, 3))
    assert [(s.date.day, s.kind) for s in intake.slots] == [
        (1, "lunch"),
        (1, "dinner"),
        (2, "lunch"),
        (2, "dinner"),
    ]  # no workouts
    first, second = intake.meetings
    assert (first.id, first.address) == ("A", "10 Post Office Sq, Boston, MA")  # earlier meeting first
    assert (second.id, second.address) == ("B", "1 Beacon St, Boston, MA")
    assert first.start == dt.datetime(2026, 12, 1, 10, 0, tzinfo=first.start.tzinfo)
    assert first.start.utcoffset() == dt.timedelta(hours=-5)  # the trip's time zone (New York offset)


def test_the_llm_gets_a_json_schema_todays_date_and_the_users_text():
    llm = FakeLLM([json.dumps(GOOD)])
    parse_trip_request("Boston next Tuesday", llm, today=TODAY)

    (call,) = llm.calls
    assert call.json_schema is not None and call.json_schema["additionalProperties"] is False
    assert "Boston next Tuesday" in call.prompt and "2026-11-14" in call.prompt


@pytest.mark.parametrize(
    "script",
    [
        [LLMError("model is down")],
        ["I could not read that."],  # not JSON
        ["[1, 2]"],  # JSON but not an object
        [json.dumps({**GOOD, "meetings": []})],  # needs at least one meeting
        [json.dumps({**GOOD, "budget_amount": -5})],
        [json.dumps({**GOOD, "check_out": "2026-11-30"})],  # check-out before check-in
        [
            json.dumps(
                {**GOOD, "meetings": [{"address": "x y z", "date": "2026-12-01", "start": "11:00", "end": "10:00"}]}
            )
        ],
        [json.dumps({k: v for k, v in GOOD.items() if k != "city"})],  # a required field is missing
    ],
)
def test_a_failing_or_invalid_llm_answer_falls_back_to_the_demo_trip(script):
    intake = parse_trip_request("Boston Dec 1 to 3", FakeLLM(script), today=TODAY)

    assert intake.is_demo and fell_back("Boston Dec 1 to 3", intake)


def test_meeting_addresses_do_not_show_up_in_the_repr():
    assert "Water St" not in repr(demo_intake())


def test_an_intake_needs_at_least_one_meeting():
    with pytest.raises(ValueError):
        TripIntake(
            city="X",
            check_in=dt.date(2026, 12, 1),
            check_out=dt.date(2026, 12, 3),
            budget={"amount": 100},
            meetings=(),
        )


def test_a_meeting_must_end_after_it_starts():
    with pytest.raises(ValueError):
        MeetingIntake(id="A", address="1 Main St", start=demo.at(17, 11), end=demo.at(17, 10))


@pytest.mark.parametrize(("written", "code"), [("$", "USD"), ("eur", "EUR"), ("€", "EUR"), (" gbp ", "GBP")])
def test_the_currency_is_normalized_to_an_iso_code(written, code):
    intake = parse_trip_request("Boston Dec 1 to 3", FakeLLM([json.dumps({**GOOD, "currency": written})]), today=TODAY)

    assert intake.budget.currency == code and not intake.is_demo


def test_every_field_is_required_in_the_schema_the_llm_sees():
    llm = FakeLLM([json.dumps(GOOD)])
    parse_trip_request("Boston Dec 1 to 3", llm, today=TODAY)
    schema = llm.calls[0].json_schema

    assert set(schema["required"]) == set(schema["properties"])
