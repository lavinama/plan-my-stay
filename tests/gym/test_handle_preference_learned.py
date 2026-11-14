import json
from pathlib import Path

from workers.gym import state
from workers.gym.handle import handle

FIXTURES = Path(__file__).resolve().parents[2] / "workers" / "gym" / "fixtures"


def _load(*parts):
    with open(FIXTURES.joinpath(*parts)) as f:
        return json.load(f)


def test_preference_learned_updates_profile_and_returns_no_reply():
    message = _load("preferences", "preferred_time_update.json")
    reply = handle(message)

    assert reply == []
    trip_id = message["trip_id"]
    assert state.get(trip_id)["profile"]["preferred_workout_time"] == "06:30"
