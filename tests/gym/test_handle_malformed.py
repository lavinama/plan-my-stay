import json
from pathlib import Path

from workers.gym.handle import handle

FIXTURES = Path(__file__).resolve().parents[2] / "workers" / "gym" / "fixtures"


def _load(*parts):
    with open(FIXTURES.joinpath(*parts)) as f:
        return json.load(f)


def test_unknown_type_produces_exactly_one_error():
    message = _load("malformed", "unknown_type.json")
    reply = handle(message)
    assert len(reply) == 1
    assert reply[0]["type"] == "error"


def test_bad_schema_version_produces_exactly_one_error():
    message = _load("malformed", "bad_schema_version.json")
    reply = handle(message)
    assert len(reply) == 1
    assert reply[0]["type"] == "error"


def test_error_type_message_gets_no_reply():
    message = {
        "schema_version": "1.0",
        "type": "error",
        "message_id": "11111111-1111-1111-1111-111111111111",
        "reply_to": None,
        "trip_id": "trip-1",
        "user_id": "user-1",
        "state_version": 1,
        "from": "verifier",
        "to": ["gym"],
        "sent_at": "2026-11-14T16:00:00-05:00",
        "summary": "Something went wrong elsewhere",
        "payload": {"code": "some_code", "bad_message_id": None, "fields": []},
    }
    assert handle(message) == []
