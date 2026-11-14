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
    stay_lat, stay_lng = stay_pick["payload"]["lat"], stay_pick["payload"]["lng"]

    # Tight tolerance against the stay: near_stay.json's gyms sit within
    # about 0.001 degrees of it.
    assert abs(place["lat"] - stay_lat) < 0.005
    assert abs(place["lng"] - stay_lng) < 0.005

    # And it must be the near_stay.json fixture, not a zone discovered
    # for one of the meetings (meeting B sits in zone z1, about 0.03
    # degrees from the stay -- far outside the tolerance above, but
    # checked explicitly here so a wrong zone lookup fails loudly).
    meeting_b = next(a for a in request["payload"]["anchors"] if a["id"] == "B")
    dist_to_stay = (place["lat"] - stay_lat) ** 2 + (place["lng"] - stay_lng) ** 2
    dist_to_meeting_b = (place["lat"] - meeting_b["lat"]) ** 2 + (place["lng"] - meeting_b["lng"]) ** 2
    assert dist_to_stay < dist_to_meeting_b

    near_stay_gyms = {g["name"] for g in _load("gyms", "near_stay.json")["gyms"]}
    assert place["name"] in near_stay_gyms
