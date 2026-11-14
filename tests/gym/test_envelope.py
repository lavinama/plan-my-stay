import json

from workers.gym import envelope


def _valid_message(**overrides):
    base = envelope.build_message(
        type="request",
        from_="concierge",
        to=["gym"],
        trip_id="trip-1",
        user_id="user-1",
        state_version=1,
        summary="A test message",
        payload={"foo": "bar"},
    )
    base.update(overrides)
    return base


def test_build_message_round_trips_through_parse_message():
    msg = _valid_message()
    text = json.dumps(msg)
    parsed = envelope.parse_message(text)
    assert parsed == msg


def test_parse_message_strips_sender_prefix_and_mention():
    msg = _valid_message()
    text = f"[Concierge]: @gym {json.dumps(msg)}"
    parsed = envelope.parse_message(text)
    assert parsed == msg


def test_validate_incoming_accepts_a_well_formed_message():
    assert envelope.validate_incoming(_valid_message()) == []


def test_validate_incoming_flags_missing_required_fields():
    msg = _valid_message()
    del msg["trip_id"]
    problems = envelope.validate_incoming(msg)
    assert any("trip_id" in p for p in problems)


def test_validate_incoming_flags_unknown_major_schema_version():
    msg = _valid_message(schema_version="2.0")
    problems = envelope.validate_incoming(msg)
    assert any("schema_version" in p for p in problems)


def test_validate_incoming_accepts_reply_to_null():
    msg = _valid_message(reply_to=None)
    assert envelope.validate_incoming(msg) == []


def test_error_for_builds_an_error_message_addressed_back_to_sender():
    original = _valid_message()
    err = envelope.error_for(original, "invalid_envelope", ["trip_id"])
    assert err["type"] == "error"
    assert err["to"] == ["concierge"]
    assert err["reply_to"] == original["message_id"]
    assert err["payload"]["code"] == "invalid_envelope"
    assert err["payload"]["bad_message_id"] == original["message_id"]
    assert err["payload"]["fields"] == ["trip_id"]


def test_known_types_cover_the_message_types_table():
    expected = {
        "request",
        "proposal",
        "zone_fit",
        "stay_shortlist",
        "stay_pick",
        "workout_slot",
        "preference_learned",
        "conflict",
        "verdict",
        "error",
        "route_request",
        "route_reply",
    }
    assert envelope.KNOWN_TYPES == expected
