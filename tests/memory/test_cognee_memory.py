"""CogneeMemory against the fake Cognee: where each kind of entry goes, failure behaviour, consolidation."""

import datetime as dt
import logging

import pytest

from plan_my_stay.demo import demo_state, demo_state_with_plan
from plan_my_stay.enums import Seat
from plan_my_stay.memory.cognee_client import CogneeClient, CogneeError
from plan_my_stay.memory.cognee_memory import CogneeMemory
from plan_my_stay.models import Profile

from .helpers import FakeClock, make_client
from .fake_cognee import API_KEY, DATASET_ID, TENANT_ID, FakeCognee

ALICE = "traveler-alice"
BOB = "traveler-bob"


def _pref(memory: CogneeMemory, user: str, trip: str, key: str, value, evidence: str = "e") -> None:
    memory.save_preference(user_id=user, trip_id=trip, key=key, value=value, evidence=evidence)


# --- where things are written --------------------------------------------------------------


def test_a_preference_goes_to_the_trip_session_as_plain_text_with_self_improvement_off(
    cognee_server: FakeCognee, cognee_memory: CogneeMemory
) -> None:
    _pref(cognee_memory, ALICE, "trip-1", "diet", ["vegetarian"], "asked for vegetarian")

    (request,) = cognee_server.calls("POST", "/api/v1/remember")
    assert request.form is not None
    assert request.form["session_id"] == "trip-1"  # session id = trip id
    assert request.form["self_improvement"] == "false"
    line = request.form["raw_data"]
    assert line.startswith("agent=concierge ")
    assert f"user={ALICE}" in line and "key=diet" in line and 'value=["vegetarian"]' in line and line.endswith("evidence=asked for vegetarian")


def test_the_writing_agent_is_configurable(cognee_server: FakeCognee, cognee_client: CogneeClient, clock: FakeClock) -> None:
    gym_memory = CogneeMemory(cognee_client, agent=Seat.GYM, clock=clock)

    gym_memory.save_city_fact(city="New York, NY", fact="Gym opens 06:00", source_url="https://a.test", fetched_at=clock.now)

    assert cognee_server.graph[0].startswith("agent=gym ")


def test_the_trip_state_copy_goes_to_the_trips_session(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    state = demo_state_with_plan()

    cognee_memory.save_trip_state(state)

    (line,) = cognee_server.session_answers(state.trip_id)
    assert line.startswith("agent=concierge ") and f"trip={state.trip_id}" in line and "state_version=1" in line
    assert cognee_server.graph == []


def test_a_city_fact_is_permanent_shared_memory_with_its_source_and_fetch_time(
    cognee_server: FakeCognee, cognee_memory: CogneeMemory, clock: FakeClock
) -> None:
    cognee_memory.save_city_fact(city="New York, NY", fact="Open until 22:00", source_url="https://a.test/hours", fetched_at=clock.now)

    (request,) = cognee_server.calls("POST", "/api/v1/remember")
    assert request.form is not None and "session_id" not in request.form
    (line,) = cognee_server.graph
    assert 'city="New York, NY"' in line and "source=https://a.test/hours" in line and f"fetched_at={clock.now.isoformat()}" in line


def test_a_trace_is_a_typed_trace_entry_in_the_trip_session(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    cognee_memory.log_trace(trip_id="trip-1", user_id=ALICE, agent=Seat.GYM, step="keenable_search", outcome="2 of 5 pages usable")

    (request,) = cognee_server.calls("POST", "/api/v1/remember/entry")
    assert request.json is not None
    entry = request.json["entry"]
    assert request.json["session_id"] == "trip-1"
    assert entry["type"] == "trace" and entry["origin_function"] == "keenable_search" and entry["status"] == "success"
    assert entry["method_return_value"].startswith("agent=gym ")
    assert "2 of 5 pages usable" in entry["method_return_value"]


# --- reading -------------------------------------------------------------------------------


def test_load_profile_reads_the_graph_as_raw_chunks_and_the_users_known_sessions(
    cognee_server: FakeCognee, cognee_memory: CogneeMemory
) -> None:
    _pref(cognee_memory, ALICE, "trip-1", "diet", ["vegan"])
    cognee_server.requests.clear()

    cognee_memory.load_profile(ALICE)

    graph_call, session_call = (r.json for r in cognee_server.calls("POST", "/api/v1/recall"))
    assert graph_call["scope"] == "graph" and graph_call["searchType"] == "CHUNKS" and graph_call["datasetIds"] == [DATASET_ID]
    assert ALICE in graph_call["query"]
    assert session_call["scope"] == "session" and session_call["sessionId"] == "trip-1"


def test_load_profile_for_a_user_with_no_sessions_only_searches_the_graph(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    cognee_memory.load_profile("brand-new")

    assert len(cognee_server.calls("POST", "/api/v1/recall")) == 1


def test_a_preference_is_found_after_a_restart_once_the_user_was_consolidated(cognee_server: FakeCognee, clock: FakeClock) -> None:
    with make_client() as first_client:
        _pref(CogneeMemory(first_client, clock=clock), ALICE, "trip-1", "diet", ["vegetarian"])
        CogneeMemory(first_client, clock=clock).consolidate(ALICE)  # a fresh instance finds the session through the session list

    with make_client() as second_client:
        restarted = CogneeMemory(second_client, clock=clock)  # no memory of any session
        assert restarted.load_profile(ALICE).diet == ("vegetarian",)


def test_without_consolidation_a_restarted_process_cannot_find_earlier_sessions(cognee_server: FakeCognee, clock: FakeClock) -> None:
    with make_client() as first_client:
        _pref(CogneeMemory(first_client, clock=clock), ALICE, "trip-1", "diet", ["vegetarian"])
    with make_client() as second_client:
        assert CogneeMemory(second_client, clock=clock).load_profile(ALICE) == Profile()  # documented: `improve` bridges sessions

    assert cognee_server.session_answers("trip-1")  # the text is still in Cognee, waiting for `consolidate`


def test_the_graph_and_a_session_holding_the_same_line_count_once(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    _pref(cognee_memory, ALICE, "trip-1", "diet", ["vegetarian"])
    cognee_memory.consolidate(ALICE)  # now in the graph and in the session

    assert cognee_memory.load_profile(ALICE).diet == ("vegetarian",)


def test_load_trip_state_falls_back_to_the_session_detail_when_recall_finds_nothing(
    cognee_server: FakeCognee, cognee_memory: CogneeMemory
) -> None:
    state = demo_state()
    cognee_memory.save_trip_state(state)
    cognee_server.fail_next(200, "[]", on="/recall")  # a recall that surfaces nothing

    loaded = cognee_memory.load_trip_state(user_id=state.user_id, trip_id=state.trip_id)

    assert loaded == state
    assert len(cognee_server.calls("GET", "/api/v1/sessions/")) == 1


def test_load_trip_state_does_not_fetch_the_detail_when_recall_succeeds(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    state = demo_state()
    cognee_memory.save_trip_state(state)

    cognee_memory.load_trip_state(user_id=state.user_id, trip_id=state.trip_id)

    assert cognee_server.calls("GET", "/api/v1/sessions/") == []


def test_recall_city_facts_searches_the_graph_with_the_city_and_the_query(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    cognee_memory.recall_city_facts(city="New York, NY", query="gym hours")

    (request,) = cognee_server.calls("POST", "/api/v1/recall")
    assert request.json is not None
    assert request.json["scope"] == "graph" and "New York, NY" in request.json["query"] and "gym hours" in request.json["query"]


# --- consolidate ---------------------------------------------------------------------------


def test_consolidate_bridges_only_this_users_sessions_into_the_graph(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    _pref(cognee_memory, ALICE, "alice-trip-1", "diet", ["vegan"])
    _pref(cognee_memory, ALICE, "alice-trip-2", "cuisines", ["Thai"])
    _pref(cognee_memory, BOB, "bob-trip-1", "diet", ["omnivore"])

    cognee_memory.consolidate(ALICE)

    (call,) = cognee_server.improve_calls
    assert call == {"datasetId": DATASET_ID, "sessionIds": ["alice-trip-1", "alice-trip-2"]}


def test_consolidate_finds_sessions_written_by_another_process(cognee_server: FakeCognee, clock: FakeClock) -> None:
    with make_client() as writer_client:
        writer = CogneeMemory(writer_client, clock=clock)
        _pref(writer, ALICE, "trip-a", "diet", ["vegan"])
        _pref(writer, BOB, "trip-b", "diet", ["omnivore"])
        writer.log_trace(trip_id="trip-c", user_id=ALICE, agent=Seat.GYM, step="s", outcome="o")  # a trace-only session
    with make_client() as other_client:
        CogneeMemory(other_client, clock=clock).consolidate(ALICE)

    assert [c["sessionIds"] for c in cognee_server.improve_calls] == [["trip-a", "trip-c"]]


@pytest.mark.parametrize("shape", ["list", "wrapped", "ids"])
def test_session_discovery_copes_with_the_list_shapes_we_might_get(cognee_server: FakeCognee, clock: FakeClock, shape: str) -> None:
    cognee_server.list_sessions_shape = shape
    with make_client() as client:
        _pref(CogneeMemory(client, clock=clock), ALICE, "trip-a", "diet", ["vegan"])
        CogneeMemory(client, clock=clock).consolidate(ALICE)

    assert [c["sessionIds"] for c in cognee_server.improve_calls] == [["trip-a"]]


def test_consolidate_with_nothing_to_bridge_does_not_call_improve(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    cognee_memory.consolidate("brand-new")

    assert cognee_server.improve_calls == []


def test_consolidate_still_runs_on_the_known_sessions_if_the_session_list_is_unavailable(
    cognee_server: FakeCognee, cognee_memory: CogneeMemory, caplog: pytest.LogCaptureFixture
) -> None:
    _pref(cognee_memory, ALICE, "trip-1", "diet", ["vegan"])
    cognee_server.fail_next(404, "no such route", on="/api/v1/sessions", times=1)

    with caplog.at_level(logging.WARNING, logger="plan_my_stay.memory"):
        cognee_memory.consolidate(ALICE)

    assert [c["sessionIds"] for c in cognee_server.improve_calls] == [["trip-1"]]
    assert any("session list" in r.getMessage().lower() for r in caplog.records)


def test_consolidate_raises_when_improve_fails(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    _pref(cognee_memory, ALICE, "trip-1", "diet", ["vegan"])
    cognee_server.fail_next(500, "improve broke", on="/improve")

    with pytest.raises(CogneeError) as err:
        cognee_memory.consolidate(ALICE)

    assert err.value.status == 500


# --- failure behaviour: writes raise, reads degrade -----------------------------------------


@pytest.fixture
def down(cognee_server: FakeCognee) -> FakeCognee:
    cognee_server.fail_next(500, "down", times=100)
    return cognee_server


@pytest.mark.parametrize(
    "write",
    [
        lambda m: m.save_preference(user_id=ALICE, trip_id="t", key="diet", value=["x"], evidence="e"),
        lambda m: m.save_trip_state(demo_state()),
        lambda m: m.save_city_fact(city="NY", fact="f", source_url="https://a.test", fetched_at=dt.datetime(2026, 11, 14, tzinfo=dt.UTC)),
        lambda m: m.log_trace(trip_id="t", user_id=ALICE, agent=Seat.GYM, step="s", outcome="o"),
    ],
    ids=["save_preference", "save_trip_state", "save_city_fact", "log_trace"],
)
def test_write_methods_raise_cognee_error_when_memory_is_down(down: FakeCognee, cognee_memory: CogneeMemory, write) -> None:
    with pytest.raises(CogneeError):
        write(cognee_memory)


def test_load_profile_returns_an_empty_profile_and_warns_when_recall_fails(down: FakeCognee, cognee_memory: CogneeMemory, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="plan_my_stay.memory"):
        profile = cognee_memory.load_profile(ALICE)

    assert profile == Profile()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and any("profile" in r.getMessage().lower() for r in warnings)


def test_load_profile_keeps_what_it_could_read_when_one_source_fails(cognee_server: FakeCognee, cognee_memory: CogneeMemory) -> None:
    _pref(cognee_memory, ALICE, "trip-1", "diet", ["vegan"])
    cognee_server.fail_next(500, "graph is down", on="/recall", times=2)  # the graph search fails (and its retry)

    assert cognee_memory.load_profile(ALICE).diet == ("vegan",)  # the session search still works


def test_the_other_reads_degrade_too(down: FakeCognee, cognee_memory: CogneeMemory, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="plan_my_stay.memory"):
        facts = cognee_memory.recall_city_facts(city="NY", query="gym")
        state = cognee_memory.load_trip_state(user_id=ALICE, trip_id="t")

    assert facts == [] and state is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) >= 2


def test_a_trip_the_session_store_does_not_know_is_simply_no_state(cognee_server: FakeCognee, cognee_memory: CogneeMemory, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="plan_my_stay.memory"):
        assert cognee_memory.load_trip_state(user_id=ALICE, trip_id="never-saved") is None

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []  # a normal "not found" is not a warning


# --- secrets -------------------------------------------------------------------------------


def test_no_secret_reaches_an_exception_or_a_log_record_through_any_memory_call(
    cognee_server: FakeCognee, cognee_memory: CogneeMemory, caplog: pytest.LogCaptureFixture, clock: FakeClock
) -> None:
    cognee_server.fail_next(500, f"echo {API_KEY} {TENANT_ID}", times=200)
    calls = [
        lambda: cognee_memory.load_profile(ALICE),
        lambda: cognee_memory.load_trip_state(user_id=ALICE, trip_id="t"),
        lambda: cognee_memory.recall_city_facts(city="NY", query="q"),
        lambda: cognee_memory.save_preference(user_id=ALICE, trip_id="t", key="diet", value=["x"], evidence="e"),
        lambda: cognee_memory.save_trip_state(demo_state()),
        lambda: cognee_memory.save_city_fact(city="NY", fact="f", source_url="https://a.test", fetched_at=clock.now),
        lambda: cognee_memory.log_trace(trip_id="t", user_id=ALICE, agent=Seat.GYM, step="s", outcome="o"),
        lambda: cognee_memory.consolidate(ALICE),
    ]

    shown: list[str] = []
    with caplog.at_level(logging.DEBUG):
        for call in calls:
            try:
                call()
            except CogneeError as exc:
                shown.append(f"{exc} {exc!r} {exc.body_excerpt}")

    shown.extend(f"{r.getMessage()} {r.exc_text or ''} {r.args!r}" for r in caplog.records)
    assert shown  # something was raised or logged
    assert not [s for s in shown if API_KEY in s or TENANT_ID in s]
