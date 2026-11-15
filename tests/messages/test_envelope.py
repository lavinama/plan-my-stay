"""Envelope behaviour: build_message, parse_message, serialize and error replies."""

import json
import uuid

import pytest

from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import (
    ErrorCode,
    MessageParseError,
    build_error_reply,
    build_message,
    parse_message,
    serialize,
)

TRIP = "7c3f1d2e-5a4b-4e8a-9b6d-2f1c0a8e4b73"

SLOT_PAYLOAD = {
    "date": "2026-11-17",
    "start": "17:30",
    "end": "18:15",
    "place": {"name": "Example HIIT Studio", "lat": 40.7502, "lng": -73.9987},
}


def _slot_message(**overrides):
    kwargs = dict(
        type=MessageType.WORKOUT_SLOT,
        sender=Seat.GYM,
        to=[Seat.RESTAURANT, Seat.CONCIERGE],
        trip_id=TRIP,
        user_id="demo-user-1",
        state_version=3,
        summary="Workout Tue 17 Nov 17:30 to 18:15",
        payload=SLOT_PAYLOAD,
    )
    kwargs.update(overrides)
    return build_message(**kwargs)


def test_build_message_lets_code_fill_the_envelope():
    msg = _slot_message()

    assert msg.schema_version == "1.0"
    uuid.UUID(msg.message_id)
    assert msg.sent_at.utcoffset() is not None
    assert msg.reply_to is None
    assert msg.sender is Seat.GYM
    assert msg.to == (Seat.RESTAURANT, Seat.CONCIERGE)


def test_build_message_validates_the_payload():
    with pytest.raises(MessageParseError) as err:
        _slot_message(payload={"date": "2026-11-17", "start": "5pm", "end": "18:15", "place": {"name": "x"}})
    assert err.value.code is ErrorCode.VALIDATION_FAILED
    assert any("start" in f for f in err.value.fields)


def test_serialize_is_exactly_one_json_object_with_from_alias():
    text = serialize(_slot_message())
    body = json.loads(text)

    assert text.startswith("{") and text.endswith("}")
    assert body["from"] == "gym"
    assert "sender" not in body
    assert body["type"] == "workout_slot"
    assert body["reply_to"] is None  # unknown is null, never omitted


def test_round_trip_through_parse_message():
    msg = _slot_message()
    assert parse_message(serialize(msg)) == msg


@pytest.mark.parametrize(
    "prefix",
    ["", "[Mario's Concierge]: ", "@plan-my-stay-gym ", "[Mario]: @plan-my-stay-gym @other.seat "],
)
def test_parse_strips_band_prefix_and_leading_mentions(prefix):
    msg = _slot_message()
    assert parse_message(prefix + serialize(msg)) == msg


def test_parse_ignores_unknown_payload_fields():
    body = json.loads(serialize(_slot_message()))
    body["payload"]["added_in_a_newer_minor"] = {"x": 1}
    assert parse_message(json.dumps(body)).payload.date.isoformat() == "2026-11-17"


def test_parse_accepts_a_newer_minor_version():
    body = json.loads(serialize(_slot_message()))
    body["schema_version"] = "1.7"
    assert parse_message(json.dumps(body)).schema_version == "1.7"


def test_parse_rejects_unknown_major_version():
    body = json.loads(serialize(_slot_message()))
    body["schema_version"] = "2.0"
    with pytest.raises(MessageParseError) as err:
        parse_message(json.dumps(body))
    assert err.value.code is ErrorCode.UNSUPPORTED_VERSION
    assert err.value.bad_message_id == body["message_id"]


def test_parse_rejects_unknown_type():
    body = json.loads(serialize(_slot_message()))
    body["type"] = "teleport"
    with pytest.raises(MessageParseError) as err:
        parse_message(json.dumps(body))
    assert err.value.code is ErrorCode.UNKNOWN_TYPE


@pytest.mark.parametrize("text", ["hello there", "```json\n{}\n```", '{"a": 1} trailing', "[1, 2]", ""])
def test_parse_rejects_anything_but_one_json_object(text):
    with pytest.raises(MessageParseError) as err:
        parse_message(text)
    assert err.value.code is ErrorCode.INVALID_JSON


def test_parse_reports_failing_fields_and_the_bad_message_id():
    body = json.loads(serialize(_slot_message()))
    body["state_version"] = "two"
    del body["summary"]
    with pytest.raises(MessageParseError) as err:
        parse_message(json.dumps(body))
    assert err.value.code is ErrorCode.VALIDATION_FAILED
    assert {"state_version", "summary"} <= set(err.value.fields)
    assert err.value.bad_message_id == body["message_id"]


def test_a_seat_cannot_send_a_type_it_does_not_own():
    with pytest.raises(MessageParseError) as err:
        _slot_message(sender=Seat.RESTAURANT, to=[Seat.CONCIERGE])
    assert err.value.code is ErrorCode.UNEXPECTED_SENDER


def test_every_send_names_a_recipient():
    with pytest.raises(MessageParseError):
        _slot_message(to=[])


def test_error_reply_answers_the_sender_with_code_and_failing_fields():
    body = json.loads(serialize(_slot_message()))
    del body["summary"]
    with pytest.raises(MessageParseError) as err:
        parse_message(json.dumps(body))

    reply = build_error_reply(err.value, sender=Seat.RESTAURANT, to=Seat.GYM, trip_id=TRIP, user_id="demo-user-1", state_version=3)

    assert reply.type is MessageType.ERROR
    assert reply.to == (Seat.GYM,)
    assert reply.reply_to == body["message_id"]
    assert reply.payload.code == "validation_failed"
    assert reply.payload.bad_message_id == body["message_id"]
    assert "summary" in reply.payload.fields
    assert parse_message(serialize(reply)) == reply


def test_error_reply_still_works_when_the_bad_message_id_is_not_a_uuid():
    """A sender with a garbage message_id must still get its one answer; reply_to stays null, the payload keeps the id."""
    body = json.loads(serialize(_slot_message()))
    body["message_id"] = "not-a-uuid"
    with pytest.raises(MessageParseError) as err:
        parse_message(json.dumps(body))

    reply = build_error_reply(err.value, sender=Seat.RESTAURANT, to=Seat.GYM, trip_id=TRIP, user_id="demo-user-1", state_version=3)

    assert reply.reply_to is None
    assert reply.payload.bad_message_id == "not-a-uuid"
    assert "message_id" in reply.payload.fields


def test_messages_are_immutable():
    msg = _slot_message()
    with pytest.raises(Exception):
        msg.summary = "changed"
