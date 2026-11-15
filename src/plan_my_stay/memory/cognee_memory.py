"""MemoryPort on Cognee: preferences and the trip-state copy in the trip's session, city facts in shared
permanent memory, traces as typed session entries. Session id = trip id.

Memory is best effort. Write methods and `consolidate` raise `CogneeError` when Cognee fails, so the caller
knows the write did not happen and can carry on. Read methods never raise a `CogneeError`: `load_profile`
returns an empty `Profile` (a new run must still start), `recall_city_facts` returns no facts and
`load_trip_state` returns None, each after logging a warning.

Where a preference is found again: while this process lives, in the trip sessions it wrote; after a restart,
in the permanent graph once `consolidate(user_id)` (Cognee `improve`) has bridged the user's sessions into
it between trips. Cognee keeps no author on an entry and one Cognee user serves every traveler, so a
traveler's privacy rests on the `user=` label in each entry, which every read filters on strictly.
"""

import datetime as dt
import logging
import threading
from collections.abc import Callable, Sequence

from pydantic import JsonValue

from plan_my_stay.enums import Seat
from plan_my_stay.memory import text, views
from plan_my_stay.memory.cognee_client import GRAPH_SEARCH_TYPE, CogneeClient, CogneeError, session_ids_from
from plan_my_stay.models import Profile
from plan_my_stay.state import TripState
from plan_my_stay.timeutil import now

logger = logging.getLogger(__name__)

RECALL_TOP_K = 50
MAX_SESSIONS_SCANNED = 100  # sessions expire after 7 days, so this is far more than a team's demo has


class _SessionIndex:
    """The trip sessions this process wrote for each user, so reads and `consolidate` know where to look."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_user: dict[str, dict[str, None]] = {}

    def add(self, user_id: str, trip_id: str) -> None:
        with self._lock:
            self._by_user.setdefault(user_id, {})[trip_id] = None

    def sessions(self, user_id: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._by_user.get(user_id, {}))


class CogneeMemory:
    def __init__(
        self,
        client: CogneeClient,
        *,
        agent: str | Seat = Seat.CONCIERGE,
        price_fact_ttl: dt.timedelta = views.DEFAULT_PRICE_FACT_TTL,
        clock: Callable[[], dt.datetime] = now,
    ) -> None:
        """`agent` is the name every entry this instance writes starts with (`agent=<name>`)."""
        self._client = client
        self._agent = text.normalize_agent(agent)
        self._price_fact_ttl = price_fact_ttl
        self._clock = clock
        self._sessions = _SessionIndex()

    # --- writes (raise CogneeError) ---------------------------------------------------------

    def save_preference(self, *, user_id: str, trip_id: str, key: str, value: JsonValue, evidence: str) -> None:
        line = text.preference_line(
            agent=self._agent, user_id=user_id, trip_id=trip_id, key=key, value=value, evidence=evidence, at=self._clock()
        )
        self._client.remember_text(line, session_id=trip_id, self_improvement=False)
        self._sessions.add(user_id, trip_id)

    def save_trip_state(self, state: TripState) -> None:
        line = text.trip_state_line(agent=self._agent, state=state, at=self._clock())
        self._client.remember_text(line, session_id=state.trip_id, self_improvement=False)
        self._sessions.add(state.user_id, state.trip_id)

    def save_city_fact(self, *, city: str, fact: str, source_url: str, fetched_at: dt.datetime) -> None:
        """Permanent shared memory: Cognee ingests the text and builds the graph before it answers, so this can be slow."""
        line = text.city_fact_line(agent=self._agent, city=city, fact=fact, source_url=source_url, fetched_at=fetched_at)
        self._client.remember_text(line, self_improvement=False)

    def log_trace(self, *, trip_id: str, user_id: str, agent: Seat, step: str, outcome: str) -> None:
        line = text.trace_line(agent=agent, trip_id=trip_id, user_id=user_id, step=step, outcome=outcome, at=self._clock())
        entry = {"type": "trace", "origin_function": step.strip()[:200], "status": "success", "method_return_value": line}
        self._client.remember_entry(session_id=trip_id, entry=entry)
        self._sessions.add(user_id, trip_id)

    def consolidate(self, user_id: str) -> None:
        """Between trips only (20 to 80 s): bridge this user's sessions into the permanent graph with Cognee `improve`."""
        text.require_id(user_id, "user_id")
        sessions = self._user_sessions(user_id)
        if not sessions:
            logger.info("No sessions to consolidate for the user")
            return
        self._client.improve(session_ids=sessions)

    # --- reads (never raise CogneeError) ----------------------------------------------------

    def load_profile(self, user_id: str) -> Profile:
        text.require_id(user_id, "user_id")
        texts = self._collect(f"preferences of user {user_id}", purpose="profile", graph=True, sessions=self._sessions.sessions(user_id))
        return views.build_profile(text.parse_preferences(texts), user_id)

    def load_trip_state(self, *, user_id: str, trip_id: str) -> TripState | None:
        text.require_id(user_id, "user_id")
        text.require_id(trip_id, "trip_id")
        query = f"trip state of trip {trip_id} for user {user_id}"
        texts = self._collect(query, purpose="trip state", graph=False, sessions=(trip_id,))
        state = views.latest_trip_state(text.parse_trip_states(texts), user_id=user_id, trip_id=trip_id)
        if state is not None:
            return state
        # Recall is retrieval and may miss it: the session detail lists the session's last 20 QA entries as they are.
        return views.latest_trip_state(
            text.parse_trip_states(self._session_detail_texts(trip_id)), user_id=user_id, trip_id=trip_id
        )

    def recall_city_facts(self, *, city: str, query: str) -> list[str]:
        return [record.fact for record in self.recall_city_fact_records(city=city, query=query)]

    def recall_city_fact_records(self, *, city: str, query: str) -> tuple[text.CityFactRecord, ...]:
        """Like `recall_city_facts`, with each fact's source URL and fetch time."""
        texts = self._collect(f"{city}: {query}", purpose="city facts", graph=True, sessions=())
        return views.select_city_facts(
            text.parse_city_facts(texts), city=city, query=query, now=self._clock(), price_ttl=self._price_fact_ttl
        )

    # --- helpers ----------------------------------------------------------------------------

    def _collect(self, query: str, *, purpose: str, graph: bool, sessions: Sequence[str]) -> tuple[str, ...]:
        """The text of every recall result from the graph and the given sessions. A search that fails is logged
        and skipped, so one source being down does not hide what the others found."""
        sources: list[tuple[str, str | None]] = [("graph", None)] if graph else []
        sources += [("session", session_id) for session_id in sessions]
        collected: list[str] = []
        for scope, session_id in sources:
            try:
                items = self._client.recall(
                    query,
                    session_id=session_id,
                    scope=scope,
                    search_type=GRAPH_SEARCH_TYPE if scope == "graph" else None,
                    top_k=RECALL_TOP_K,
                )
            except CogneeError as exc:
                logger.warning("Cognee recall failed while loading the %s (%s search): %s", purpose, scope, exc)
                continue
            collected.extend(text.result_texts(items))
        return tuple(collected)

    def _session_detail_texts(self, session_id: str) -> tuple[str, ...]:
        try:
            detail = self._client.get_session(session_id)
        except CogneeError as exc:
            if exc.status != 404:  # 404 is a trip that was never saved: normal, not worth a warning
                logger.warning("Cognee session read failed while loading the trip state: %s", exc)
            return ()
        # UNVERIFIED: the detail's shape: `qas` items with an `answer`, `traces` items with a `method_return_value`.
        entries = [*_as_list(detail.get("qas")), *_as_list(detail.get("traces"))]
        return text.result_texts(entries)

    def _user_sessions(self, user_id: str) -> tuple[str, ...]:
        """The user's sessions: those this process wrote, plus any other session in Cognee that holds one of their entries."""
        known = self._sessions.sessions(user_id)
        try:
            listed = session_ids_from(self._client.list_sessions())
        except CogneeError as exc:
            logger.warning("Cognee session list unavailable; consolidating only the sessions this process wrote: %s", exc)
            return known
        unknown = [session_id for session_id in listed if session_id not in known]
        if len(unknown) > MAX_SESSIONS_SCANNED:
            logger.warning("Checking only the first %d of %d sessions for the user's entries", MAX_SESSIONS_SCANNED, len(unknown))
        found = [s for s in unknown[:MAX_SESSIONS_SCANNED] if text.mentions_user(self._session_detail_texts(s), user_id)]
        return (*known, *found)


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
