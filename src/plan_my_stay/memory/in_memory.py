"""The fake MemoryPort for tests and offline runs.

It stores the same text lines CogneeMemory writes and reads them back through the same parsers and
selection rules (`text`, `views`), so a seat tested against it sees what it would see against Cognee:
newest value per key, strict per-user filtering, expiring price facts, unknown preference keys kept as
extras. `InMemoryMemory()` needs no arguments. To simulate two processes sharing one Cognee dataset, give
two instances the same `MemoryStore`. Differences from Cognee: everything is visible at once (there is no
session-versus-graph split), `consolidate` only records the call, and nothing can fail.
"""

import datetime as dt
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import JsonValue

from plan_my_stay.enums import Seat
from plan_my_stay.memory import text, views
from plan_my_stay.models import Profile
from plan_my_stay.state import TripState
from plan_my_stay.timeutil import now


@dataclass
class MemoryStore:
    """What an InMemoryMemory keeps, as the text lines Cognee would hold. Share one between instances to share data."""

    sessions: dict[str, list[str]] = field(default_factory=dict)  # per trip: preference and trip-state lines
    traces: dict[str, list[str]] = field(default_factory=dict)  # per trip: trace lines
    permanent: list[str] = field(default_factory=list)  # city facts
    consolidated: list[str] = field(default_factory=list)  # user ids `consolidate` was called with
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class InMemoryMemory:
    def __init__(
        self,
        *,
        store: MemoryStore | None = None,
        agent: str | Seat = Seat.CONCIERGE,
        price_fact_ttl: dt.timedelta = views.DEFAULT_PRICE_FACT_TTL,
        clock: Callable[[], dt.datetime] = now,
    ) -> None:
        self._store = store if store is not None else MemoryStore()
        self._agent = text.normalize_agent(agent)
        self._price_fact_ttl = price_fact_ttl
        self._clock = clock

    # --- inspection for tests ---------------------------------------------------------------

    def trace_lines(self, trip_id: str) -> tuple[str, ...]:
        """The traces logged for a trip, as written (each starts with `agent=<name>`)."""
        with self._store.lock:
            return tuple(self._store.traces.get(trip_id, ()))

    @property
    def consolidated_users(self) -> tuple[str, ...]:
        """The user ids `consolidate` was called with, in order."""
        with self._store.lock:
            return tuple(self._store.consolidated)

    # --- MemoryPort -------------------------------------------------------------------------

    def load_profile(self, user_id: str) -> Profile:
        text.require_id(user_id, "user_id")
        with self._store.lock:
            lines = [line for session in self._store.sessions.values() for line in session]
        return views.build_profile(text.parse_preferences(lines), user_id)

    def save_preference(self, *, user_id: str, trip_id: str, key: str, value: JsonValue, evidence: str) -> None:
        line = text.preference_line(
            agent=self._agent, user_id=user_id, trip_id=trip_id, key=key, value=value, evidence=evidence, at=self._clock()
        )
        self._append(self._store.sessions, trip_id, line)

    def save_trip_state(self, state: TripState) -> None:
        line = text.trip_state_line(agent=self._agent, state=state, at=self._clock())
        self._append(self._store.sessions, state.trip_id, line)

    def load_trip_state(self, *, user_id: str, trip_id: str) -> TripState | None:
        text.require_id(user_id, "user_id")
        text.require_id(trip_id, "trip_id")
        with self._store.lock:
            lines = list(self._store.sessions.get(trip_id, ()))
        return views.latest_trip_state(text.parse_trip_states(lines), user_id=user_id, trip_id=trip_id)

    def save_city_fact(self, *, city: str, fact: str, source_url: str, fetched_at: dt.datetime) -> None:
        line = text.city_fact_line(agent=self._agent, city=city, fact=fact, source_url=source_url, fetched_at=fetched_at)
        with self._store.lock:
            self._store.permanent.append(line)

    def recall_city_facts(self, *, city: str, query: str) -> list[str]:
        return [record.fact for record in self.recall_city_fact_records(city=city, query=query)]

    def recall_city_fact_records(self, *, city: str, query: str) -> tuple[text.CityFactRecord, ...]:
        """Like `recall_city_facts`, with each fact's source URL and fetch time."""
        with self._store.lock:
            lines = list(self._store.permanent)
        return views.select_city_facts(
            text.parse_city_facts(lines), city=city, query=query, now=self._clock(), price_ttl=self._price_fact_ttl
        )

    def log_trace(self, *, trip_id: str, user_id: str, agent: Seat, step: str, outcome: str) -> None:
        line = text.trace_line(agent=agent, trip_id=trip_id, user_id=user_id, step=step, outcome=outcome, at=self._clock())
        self._append(self._store.traces, trip_id, line)

    def consolidate(self, user_id: str) -> None:
        text.require_id(user_id, "user_id")
        with self._store.lock:
            self._store.consolidated.append(user_id)

    def _append(self, bucket: dict[str, list[str]], trip_id: str, line: str) -> None:
        with self._store.lock:
            bucket.setdefault(trip_id, []).append(line)
