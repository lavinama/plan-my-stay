import json
from pathlib import Path

from workers.gym.handle import handle

FIXTURES = Path(__file__).resolve().parents[2] / "workers" / "gym" / "fixtures"


def _load(*parts):
    with open(FIXTURES.joinpath(*parts)) as f:
        return json.load(f)


def test_request_produces_the_demo_tuesday_workout_slot():
    request = _load("requests", "demo_request.json")
    messages = handle(request)

    workout_slots = [m for m in messages if m["type"] == "workout_slot"]
    assert len(workout_slots) == 1
    payload = workout_slots[0]["payload"]
    assert payload["start"] == "17:30"
    assert payload["end"] == "18:15"

    # The place is near meeting B (40.7502, -73.9987), not the stay.
    place = payload["place"]
    assert abs(place["lat"] - 40.7502) < 0.02
    assert abs(place["lng"] - (-73.9987)) < 0.02


def test_request_proposal_item_is_exact_precision():
    request = _load("requests", "demo_request.json")
    messages = handle(request)

    proposals = [m for m in messages if m["type"] == "proposal"]
    assert len(proposals) == 1
    item = proposals[0]["payload"]["items"][0]
    assert item["location_precision"] == "exact"


def test_one_zone_fit_message_per_requested_zone():
    request = _load("requests", "demo_request.json")
    messages = handle(request)

    zone_fit_messages = [m for m in messages if m["type"] == "zone_fit"]
    requested_zone_ids = {z["id"] for z in request["payload"]["zones"]}
    assert {m["payload"]["zone_id"] for m in zone_fit_messages} == requested_zone_ids
    assert all(m["to"] == ["stay"] for m in zone_fit_messages)
