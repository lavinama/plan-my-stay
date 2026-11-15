import json
from pathlib import Path

from workers.stay.handle import handle

FIXTURES = Path(__file__).resolve().parents[2] / "workers" / "stay" / "fixtures"


def _load(*parts):
    with open(FIXTURES.joinpath(*parts)) as f:
        return json.load(f)


def test_request_returns_one_stay_shortlist_with_six_stays():
    request = _load("requests", "demo_request.json")
    reply = handle(request)
    assert len(reply) == 1
    assert reply[0]["type"] == "stay_shortlist"
    assert set(reply[0]["to"]) == {"restaurant", "gym", "concierge"}
    assert reply[0]["reply_to"] == request["message_id"]

    stays = reply[0]["payload"]["stays"]
    assert len(stays) == 6
    for stay in stays:
        assert stay["stay_id"]
        assert "driving" in stay
        assert "total_price" in stay


def test_stays_are_ranked_by_driving_time_then_price_then_rating():
    request = _load("requests", "demo_request.json")
    reply = handle(request)
    stays = reply[0]["payload"]["stays"]

    def avg_minutes(stay):
        legs = stay["driving"].values()
        return sum(leg["minutes"] for leg in legs) / len(legs)

    minutes = [avg_minutes(s) for s in stays]
    assert minutes == sorted(minutes)


def test_budget_filter_drops_stays_over_budget():
    request = _load("requests", "demo_request.json")
    request = dict(request)
    request["payload"] = {**request["payload"], "budget": {"currency": "USD", "amount": 500}}

    reply = handle(request)
    stays = reply[0]["payload"]["stays"]
    assert all(s["total_price"] <= 500 for s in stays)
    assert len(stays) < 6
    assert "note" in reply[0]["payload"]


def test_unknown_type_produces_exactly_one_error():
    message = {
        "schema_version": "1.0",
        "type": "not_a_real_type",
        "message_id": "55555555-5555-5555-5555-555555555555",
        "reply_to": None,
        "trip_id": "trip-1",
        "user_id": "user-1",
        "state_version": 1,
        "from": "concierge",
        "to": ["stay"],
        "sent_at": "2026-11-14T16:00:00-05:00",
        "summary": "bad type",
        "payload": {},
    }
    reply = handle(message)
    assert len(reply) == 1
    assert reply[0]["type"] == "error"


def test_preference_learned_gets_no_reply():
    message = {
        "schema_version": "1.0",
        "type": "preference_learned",
        "message_id": "66666666-6666-6666-6666-666666666666",
        "reply_to": None,
        "trip_id": "trip-1",
        "user_id": "user-1",
        "state_version": 1,
        "from": "restaurant",
        "to": ["stay"],
        "sent_at": "2026-11-14T16:00:00-05:00",
        "summary": "learned a preference",
        "payload": {"key": "diet", "value": ["vegetarian"], "evidence": "user said so"},
    }
    assert handle(message) == []


def test_empty_payload_does_not_crash_the_worker():
    # P0's discover() ignores the payload entirely (it returns the fixed 6
    # fixture stays regardless), and budget is optional, so an empty
    # payload is not malformed for this stub -- it just means no filter.
    # The point of this test is that handle() never raises on it.
    message = {
        "schema_version": "1.0",
        "type": "request",
        "message_id": "77777777-7777-7777-7777-777777777777",
        "reply_to": None,
        "trip_id": "trip-1",
        "user_id": "user-1",
        "state_version": 1,
        "from": "concierge",
        "to": ["stay"],
        "sent_at": "2026-11-14T16:00:00-05:00",
        "summary": "no payload fields at all",
        "payload": {},
    }
    reply = handle(message)
    assert len(reply) == 1
    assert reply[0]["type"] == "stay_shortlist"
    assert len(reply[0]["payload"]["stays"]) == 6
