"""Regression guard: a stay_pick must never touch an already-finalized date.

docs/PLAN.md's own demo depends on Tuesday's workout (near meeting B)
staying put once it's been sent; only Wednesday, which was still pending,
should move when the stay is picked.
"""

import copy
import json
from pathlib import Path

from workers.gym import state
from workers.gym.handle import handle

FIXTURES = Path(__file__).resolve().parents[2] / "workers" / "gym" / "fixtures"


def _load(*parts):
    with open(FIXTURES.joinpath(*parts)) as f:
        return json.load(f)


def test_stay_pick_does_not_change_the_tuesday_item():
    request = _load("requests", "demo_request.json")
    handle(request)

    trip_id = request["trip_id"]
    tuesday_items_before = copy.deepcopy(state.get(trip_id)["items"])
    assert tuesday_items_before  # Tuesday's item was in fact stored

    stay_pick = _load("stay_pick", "demo_stay_pick.json")
    messages = handle(stay_pick)

    # The stay_pick reply must carry no Tuesday-dated message.
    for message in messages:
        if message["type"] == "workout_slot":
            assert message["payload"]["date"] != "2026-11-17"
        if message["type"] == "proposal":
            for item in message["payload"]["items"]:
                assert item["when"]["date"] != "2026-11-17"

    tuesday_items_after = state.get(trip_id)["items"]
    for item_id, stored in tuesday_items_before.items():
        if stored["date"] == "2026-11-17":
            assert tuesday_items_after[item_id] == stored
    assert "2026-11-17" in state.get(trip_id)["final_dates"]
