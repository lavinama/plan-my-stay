"""A request can carry the meetings (address and time) instead of coordinates, so the Concierge can talk only to the
Stay agent: Stay finds the coordinates itself. Meetings carry no title and no attendees."""

import json

import pytest
from pydantic import ValidationError

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import MessageParseError, build_message, examples, parse_message, serialize
from plan_my_stay.models import Meeting

MEETING = {"id": "B", "address": "368 9th Ave, New York, NY 10001", "start": "2026-11-17T16:00:00-05:00",
           "end": "2026-11-17T17:00:00-05:00", "counts_for_stay": True}


def _request(**payload_overrides):
    payload = {
        "city": "New York, NY", "dates": {"check_in": "2026-11-17", "check_out": "2026-11-19"},
        "budget": {"currency": "USD", "amount": 1000}, "roster": ["stay"], "response_type": "stay_shortlist",
        **payload_overrides,
    }
    return build_message(type=MessageType.REQUEST, sender=Seat.CONCIERGE, to=[Seat.STAY], trip_id=demo.TRIP_ID,
                         user_id=demo.USER_ID, state_version=1, summary="Find stays", payload=payload)


def test_a_meeting_is_an_address_and_a_time_window():
    meeting = Meeting.model_validate(MEETING)

    assert (meeting.id, meeting.counts_for_stay) == ("B", True)
    assert meeting.address.startswith("368 9th Ave")


def test_a_meeting_has_no_title_or_attendees_even_if_a_sender_adds_them():
    meeting = Meeting.model_validate({**MEETING, "title": "Board meeting", "attendees": ["alice@example.test"]})

    dumped = meeting.model_dump(mode="json")
    assert "title" not in dumped and "attendees" not in dumped
    assert "Board meeting" not in json.dumps(dumped)


def test_a_meeting_must_end_after_it_starts_and_have_an_address():
    with pytest.raises(ValidationError):
        Meeting.model_validate({**MEETING, "end": MEETING["start"]})
    with pytest.raises(ValidationError):
        Meeting.model_validate({**MEETING, "address": "  "})


def test_a_request_with_meetings_and_no_anchors_is_valid_and_round_trips():
    msg = _request(meetings=[MEETING])

    assert msg.payload.anchors == () and msg.payload.meetings[0].id == "B"
    assert parse_message(serialize(msg)) == msg


def test_a_request_with_anchors_only_is_still_valid():
    anchors = [a.model_dump(mode="json") for a in demo.demo_anchors()]
    assert _request(anchors=anchors).payload.meetings == ()


def test_a_request_with_neither_anchors_nor_meetings_is_rejected():
    with pytest.raises(MessageParseError) as err:
        _request()
    assert err.value.code.value == "validation_failed"


def test_the_contract_examples_include_a_stay_request_with_meetings():
    example = examples.valid_examples()["stay_request"]
    msg = parse_message(json.dumps(example))

    assert msg.type is MessageType.REQUEST and msg.to == (Seat.STAY,)
    assert msg.payload.roster == (Seat.STAY,) and msg.payload.budget.amount == 1000
    assert {m.id for m in msg.payload.meetings} == {"A", "B", "C"} and msg.payload.anchors == ()
    assert [m.counts_for_stay for m in msg.payload.meetings] == [False, True, True]  # A is before check-in
