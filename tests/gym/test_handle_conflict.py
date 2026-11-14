import json
from pathlib import Path

from workers.gym.handle import handle

FIXTURES = Path(__file__).resolve().parents[2] / "workers" / "gym" / "fixtures"


def _load(*parts):
    with open(FIXTURES.joinpath(*parts)) as f:
        return json.load(f)


def test_conflict_on_a_real_item_swaps_in_a_new_candidate_same_slot():
    request = _load("requests", "demo_request.json")
    request_messages = handle(request)
    original_item = [m for m in request_messages if m["type"] == "proposal"][0][
        "payload"
    ]["items"][0]

    conflict = _load("conflicts", "hours_conflict.json")
    conflict["payload"]["item_id"] = original_item["item_id"]

    reply = handle(conflict)
    assert len(reply) == 1
    assert reply[0]["type"] == "proposal"

    new_item = reply[0]["payload"]["items"][0]
    assert new_item["item_id"] != original_item["item_id"]
    assert new_item["slot_id"] == original_item["slot_id"]
    assert new_item["name"] != original_item["name"]


def test_conflict_on_an_unknown_item_returns_one_error():
    conflict = _load("conflicts", "hours_conflict.json")
    conflict["payload"]["item_id"] = "gym-does-not-exist"

    reply = handle(conflict)
    assert len(reply) == 1
    assert reply[0]["type"] == "error"
    assert reply[0]["payload"]["code"] == "unknown_item"
