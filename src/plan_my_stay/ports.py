"""Interfaces the seats depend on, so each can be built and tested against a fake.

`memory/` implements MemoryPort (Cognee, plus an in-memory fake). The Concierge's LLM is an
LLMPort (a frontier model, plus a fake). Seats take these in their constructor and never
import a vendor client directly.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import JsonValue

from plan_my_stay.enums import Seat
from plan_my_stay.models import Profile
from plan_my_stay.state import TripState


class MemoryPort(Protocol):
    """The shared memory (Cognee): preferences, a copy of the trip state, city facts, agent traces.

    Only the Concierge writes preferences and the trip-state copy. Calls are blocking and may
    fail; callers treat memory as best-effort and keep planning if it is down.
    """

    def load_profile(self, user_id: str) -> Profile:
        """What is remembered about this traveler (empty Profile for a new user)."""
        ...

    def save_preference(self, *, user_id: str, trip_id: str, key: str, value: JsonValue, evidence: str) -> None:
        """Write one learned preference as plain QA text to the trip session (self_improvement off)."""
        ...

    def save_trip_state(self, state: TripState) -> None:
        """Keep a copy of the trip state in the trip's session. The messages stay the source of truth."""
        ...

    def load_trip_state(self, *, user_id: str, trip_id: str) -> TripState | None:
        """The latest saved copy, for a late-joining seat or a later run."""
        ...

    def save_city_fact(self, *, city: str, fact: str, source_url: str, fetched_at: dt.datetime) -> None:
        """A shared place fact (hours, price level, neighborhood trait) with its source and fetch time."""
        ...

    def recall_city_facts(self, *, city: str, query: str) -> Sequence[str]:
        """Shared facts about a city relevant to `query` (plain text), newest first."""
        ...

    def log_trace(self, *, trip_id: str, user_id: str, agent: Seat, step: str, outcome: str) -> None:
        """One agent step (a search, a page read, a conflict, an accept or reject) as an `agent=<name>` session entry."""
        ...

    def consolidate(self, user_id: str) -> None:
        """Between trips only, never in a request: bridge session entries into permanent memory (Cognee `improve`)."""
        ...


class LLMPort(Protocol):
    """A frontier model. The Concierge calls it only to parse the user's request, arbitrate, and write the itinerary."""

    def complete(self, *, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        """Return the model's text. With `json_schema`, the result is one JSON object matching it (structured output)."""
        ...
