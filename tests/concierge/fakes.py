"""Test doubles for the Concierge: a minimal MemoryPort that can be switched off.

Nothing here touches the network. `FakeMemory` records what the Concierge writes so tests can
assert on it, and `down=True` makes every call raise, to prove memory is best effort.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any

from pydantic import JsonValue

from plan_my_stay.enums import Seat
from plan_my_stay.models import Profile
from plan_my_stay.state import TripState


class FakeMemory:
    def __init__(self, profile: Profile | None = None, *, down: bool = False) -> None:
        self.profile = profile if profile is not None else Profile()
        self.down = down
        self.preferences: list[dict[str, Any]] = []
        self.saved_states: list[TripState] = []
        self.traces: list[dict[str, Any]] = []
        self.profile_loads: list[str] = []

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("memory is down")

    def load_profile(self, user_id: str) -> Profile:
        self._check()
        self.profile_loads.append(user_id)
        return self.profile

    def save_preference(self, *, user_id: str, trip_id: str, key: str, value: JsonValue, evidence: str) -> None:
        self._check()
        self.preferences.append(
            {"user_id": user_id, "trip_id": trip_id, "key": key, "value": value, "evidence": evidence}
        )

    def save_trip_state(self, state: TripState) -> None:
        self._check()
        self.saved_states.append(state)

    def load_trip_state(self, *, user_id: str, trip_id: str) -> TripState | None:
        self._check()
        return self.saved_states[-1] if self.saved_states else None

    def save_city_fact(self, *, city: str, fact: str, source_url: str, fetched_at: dt.datetime) -> None:
        self._check()

    def recall_city_facts(self, *, city: str, query: str) -> Sequence[str]:
        self._check()
        return ()

    def log_trace(self, *, trip_id: str, user_id: str, agent: Seat, step: str, outcome: str) -> None:
        self._check()
        self.traces.append({"trip_id": trip_id, "user_id": user_id, "agent": agent, "step": step, "outcome": outcome})

    def consolidate(self, user_id: str) -> None:
        self._check()
