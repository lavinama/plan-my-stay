import json
from pathlib import Path

from workers.gym.handle import handle

FIXTURES = Path(__file__).resolve().parents[2] / "workers" / "gym" / "fixtures"


def _load(*parts):
    with open(FIXTURES.joinpath(*parts)) as f:
        return json.load(f)


def test_no_wednesday_slot_before_stay_pick():
    request = _load("requests", "demo_request.json")
    messages = handle(request)

    workout_slots = [m for m in messages if m["type"] == "workout_slot"]
    assert len(workout_slots) == 1
    assert workout_slots[0]["payload"]["date"] == "2026-11-17"


def test_stay_pick_resolves_the_wednesday_slot():
    request = _load("requests", "demo_request.json")
    handle(request)

    stay_pick = _load("stay_pick", "demo_stay_pick.json")
    messages = handle(stay_pick)

    workout_slots = [m for m in messages if m["type"] == "workout_slot"]
    assert len(workout_slots) == 1
    payload = workout_slots[0]["payload"]
    assert payload["date"] == "2026-11-18"
    assert payload["start"] == "07:00"
    assert payload["end"] == "07:45"
    assert workout_slots[0]["reply_to"] == stay_pick["message_id"]

    place = payload["place"]
    assert abs(place["lat"] - stay_pick["payload"]["lat"]) < 0.02
    assert abs(place["lng"] - stay_pick["payload"]["lng"]) < 0.02
