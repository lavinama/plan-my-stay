"""CogneeClient: request shapes, auth headers, dataset addressing, retries and error hygiene."""

import logging

import httpx
import pytest

from plan_my_stay.config import Secret
from plan_my_stay.memory import cognee_client
from plan_my_stay.memory.cognee_client import CogneeClient, CogneeError

from .helpers import make_client
from .fake_cognee import API_KEY, BASE_URL, DATASET_ID, TENANT_ID, FakeCognee

QA = {"type": "qa", "question": "q?", "answer": "a."}


# --- request shapes ------------------------------------------------------------------------


def test_every_call_sends_both_auth_headers(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.remember_entry(session_id="s1", entry=QA)
    cognee_client.remember_text("hello", session_id="s1")
    cognee_client.get_session("s1")
    cognee_client.list_sessions()
    cognee_client.recall("q", session_id="s1")
    cognee_client.improve(session_ids=["s1"])
    cognee_client.create_dataset("main")
    cognee_client.delete_session("s1")

    assert len(cognee_server.requests) == 8
    for request in cognee_server.requests:
        assert request.headers["x-api-key"] == API_KEY
        assert request.headers["x-tenant-id"] == TENANT_ID


def test_json_calls_declare_json_and_the_multipart_call_lets_httpx_set_the_boundary(
    cognee_server: FakeCognee, cognee_client: CogneeClient
) -> None:
    cognee_client.remember_entry(session_id="s1", entry=QA)
    cognee_client.remember_text("hello", session_id="s1")

    entry, text = cognee_server.requests
    assert entry.headers["content-type"] == "application/json"
    assert text.headers["content-type"].startswith("multipart/form-data; boundary=")


def test_the_typed_entry_addresses_the_dataset_by_its_uuid(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    result = cognee_client.remember_entry(session_id="trip-1", entry=QA)

    (request,) = cognee_server.calls("POST", "/api/v1/remember/entry")
    assert request.json == {"dataset_id": DATASET_ID, "session_id": "trip-1", "entry": QA}
    assert "dataset_name" not in request.json
    assert result["status"] == "session_stored"


def test_plain_text_is_multipart_with_the_dataset_id_session_and_self_improvement_off(
    cognee_server: FakeCognee, cognee_client: CogneeClient
) -> None:
    cognee_client.remember_text("agent=concierge hello", session_id="trip-1")

    (request,) = cognee_server.calls("POST", "/api/v1/remember")
    assert request.form == {
        "datasetId": DATASET_ID,
        "session_id": "trip-1",
        "raw_data": "agent=concierge hello",
        "self_improvement": "false",
    }
    assert "datasetName" not in request.form


def test_plain_text_without_a_session_is_permanent_memory(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.remember_text("a city fact")

    (request,) = cognee_server.calls("POST", "/api/v1/remember")
    assert "session_id" not in request.form
    assert request.form["self_improvement"] == "false"
    assert cognee_server.graph == ["a city fact"]


def test_plain_text_keeps_non_ascii_text_intact(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.remember_text("Café Bärlin ☕ 東京", session_id="s")

    assert cognee_server.session_answers("s") == ["Café Bärlin ☕ 東京"]


def test_recall_addresses_the_dataset_by_uuid_and_passes_session_scope_and_search_type(
    cognee_server: FakeCognee, cognee_client: CogneeClient
) -> None:
    cognee_client.recall("what does u1 eat", session_id="trip-1", scope="session", search_type="CHUNKS", top_k=40)

    (request,) = cognee_server.calls("POST", "/api/v1/recall")
    assert request.json == {
        "query": "what does u1 eat",
        "datasetIds": [DATASET_ID],
        "sessionId": "trip-1",
        "scope": "session",
        "searchType": "CHUNKS",
        "topK": 40,
    }


def test_recall_leaves_out_what_it_is_not_given(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.recall("q")

    (request,) = cognee_server.calls("POST", "/api/v1/recall")
    assert request.json == {"query": "q", "datasetIds": [DATASET_ID]}


def test_recall_returns_the_result_items(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.remember_text("one", session_id="s")

    items = cognee_client.recall("q", session_id="s", scope="session")

    assert [i["answer"] for i in items] == ["one"]


def test_improve_sends_the_dataset_id_and_session_ids(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    result = cognee_client.improve(session_ids=["s1", "s2"])

    (request,) = cognee_server.calls("POST", "/api/v1/improve")
    assert request.json == {"datasetId": DATASET_ID, "sessionIds": ["s1", "s2"]}
    assert result["status"] == "completed"


def test_get_session_quotes_the_id_into_the_path(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.remember_text("x", session_id="a/b c")

    detail = cognee_client.get_session("a/b c")

    assert detail["qas"][0]["answer"] == "x"
    assert cognee_server.calls("GET", "/api/v1/sessions/")[0].raw_path == "/api/v1/sessions/a%2Fb%20c"


def test_list_sessions_asks_for_all_of_them(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.list_sessions()

    (request,) = cognee_server.calls("GET", "/api/v1/sessions")
    assert request.query == "range=all"


def test_create_dataset_posts_the_name_and_returns_the_new_id(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    created = cognee_client.create_dataset("plan-my-stay")

    (request,) = cognee_server.calls("POST", "/api/v1/datasets/")
    assert request.json == {"name": "plan-my-stay"}
    assert created["id"] == DATASET_ID


def test_the_deletes_hit_the_documented_paths(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_client.remember_text("x", session_id="s1")

    assert cognee_client.delete_session("s1") == {"session_id": "s1", "deleted": True}
    assert cognee_client.delete_dataset(DATASET_ID) == {}

    assert [(r.method, r.path) for r in cognee_server.requests[1:]] == [
        ("DELETE", "/api/v1/sessions/s1"),
        ("DELETE", f"/api/v1/datasets/{DATASET_ID}"),
    ]


def test_dataset_ref_is_the_one_place_that_names_the_dataset_parameter(cognee_client: CogneeClient) -> None:
    assert cognee_client._dataset_ref("remember_entry") == {"dataset_id": DATASET_ID}
    assert cognee_client._dataset_ref("remember") == {"datasetId": DATASET_ID}
    assert cognee_client._dataset_ref("recall") == {"datasetIds": [DATASET_ID]}
    assert cognee_client._dataset_ref("improve") == {"datasetId": DATASET_ID}
    with pytest.raises(ValueError):
        cognee_client._dataset_ref("nonsense")  # type: ignore[arg-type]


def test_a_dataset_the_tenant_does_not_know_is_a_clear_error(cognee_server: FakeCognee) -> None:
    with make_client(dataset_id="11111111-2222-4333-8444-555555555555") as client, pytest.raises(CogneeError) as err:
        client.remember_entry(session_id="s", entry=QA)

    assert err.value.status == 404


# --- construction --------------------------------------------------------------------------


def test_the_dataset_must_be_a_uuid_not_a_name() -> None:
    with pytest.raises(ValueError, match="UUID"):
        make_client(dataset_id="plan-my-stay")


def test_secrets_must_be_wrapped() -> None:
    with pytest.raises(TypeError, match="Secret"):
        make_client(api_key=API_KEY)
    with pytest.raises(TypeError, match="Secret"):
        make_client(tenant_id=TENANT_ID)


@pytest.mark.parametrize("field", ["api_key", "tenant_id"])
def test_empty_secrets_are_rejected_at_startup(field: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        make_client(**{field: Secret("  ")})


@pytest.mark.parametrize("url", ["", "tenant.cognee.test", "ftp://tenant.cognee.test"])
def test_the_base_url_must_be_http_or_https(url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        make_client(base_url=url)


def test_a_trailing_slash_on_the_base_url_is_fine(cognee_server: FakeCognee) -> None:
    with make_client(base_url=BASE_URL + "/") as client:
        client.list_sessions()

    assert cognee_server.requests[0].path == "/api/v1/sessions"


def test_an_injected_http_client_is_used_and_not_closed() -> None:
    fake = FakeCognee()
    http = httpx.Client(transport=httpx.MockTransport(fake.handle))
    client = CogneeClient(
        base_url=BASE_URL, api_key=Secret(API_KEY), tenant_id=Secret(TENANT_ID), dataset_id=DATASET_ID, http=http, retry_delay_s=0
    )

    client.list_sessions()
    client.close()

    assert not http.is_closed
    assert len(fake.requests) == 1
    http.close()


def test_the_client_closes_the_http_client_it_made(cognee_server: FakeCognee) -> None:
    client = make_client()
    inner = client._http

    client.close()

    assert inner.is_closed


def test_the_repr_hides_the_secrets() -> None:
    client = make_client()

    assert API_KEY not in repr(client) and TENANT_ID not in repr(client)
    client.close()


# --- retries -------------------------------------------------------------------------------


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(cognee_client.time, "sleep", recorded.append)
    return recorded


def test_one_server_error_is_retried_once_after_the_delay(cognee_server: FakeCognee, sleeps: list[float]) -> None:
    cognee_server.fail_next(503)
    with make_client(retry_delay_s=1.5) as client:
        client.remember_text("x", session_id="s")

    assert len(cognee_server.requests) == 2
    assert sleeps == [1.5]
    assert cognee_server.session_answers("s") == ["x"]


def test_a_timeout_is_retried_once(cognee_server: FakeCognee, cognee_client: CogneeClient, sleeps: list[float]) -> None:
    cognee_server.time_out_next()

    cognee_client.recall("q")

    assert len(cognee_server.requests) == 2


def test_two_failures_in_a_row_raise_after_exactly_one_retry(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_server.fail_next(500, times=5)

    with pytest.raises(CogneeError) as err:
        cognee_client.recall("q")

    assert err.value.status == 500
    assert len(cognee_server.requests) == 2


def test_two_timeouts_raise_a_cognee_error_without_a_status(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_server.time_out_next(times=5)

    with pytest.raises(CogneeError, match="timed out") as err:
        cognee_client.recall("q")

    assert err.value.status is None
    assert len(cognee_server.requests) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_client_errors_are_not_retried(cognee_server: FakeCognee, cognee_client: CogneeClient, status: int) -> None:
    cognee_server.fail_next(status, "nope", times=3)

    with pytest.raises(CogneeError) as err:
        cognee_client.recall("q")

    assert err.value.status == status
    assert len(cognee_server.requests) == 1


def test_a_refused_connection_is_an_error_without_a_retry(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_server.connect_error_next(times=3)

    with pytest.raises(CogneeError, match="ConnectError"):
        cognee_client.recall("q")

    assert len(cognee_server.requests) == 1


def test_improve_is_never_retried_because_it_takes_up_to_80_seconds(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_server.time_out_next(times=3)

    with pytest.raises(CogneeError):
        cognee_client.improve(session_ids=["s"])

    assert len(cognee_server.requests) == 1


def test_improve_and_permanent_writes_get_longer_timeouts_than_a_recall(
    cognee_server: FakeCognee, cognee_client: CogneeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    real_request = cognee_client._http.request

    def spy(method: str, url: str, **kwargs):
        seen[url.rsplit("/", 1)[-1]] = kwargs.get("timeout")
        return real_request(method, url, **kwargs)

    monkeypatch.setattr(cognee_client._http, "request", spy)

    cognee_client.recall("q")
    cognee_client.improve(session_ids=["s"])
    cognee_client.remember_text("permanent")

    assert seen["improve"] >= 120 and seen["recall"] <= 60
    assert seen["remember"] >= 120  # the last write above has no session: it builds the graph


# --- bad answers ---------------------------------------------------------------------------


def test_a_200_that_is_not_json_is_an_error(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_server.fail_next(200, "<html>maintenance</html>")  # fail_next scripts any reply, 200 included

    with pytest.raises(CogneeError, match="not JSON"):
        cognee_client.recall("q")


def test_recall_must_answer_with_a_list(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_server.fail_next(200, '{"results": []}')

    with pytest.raises(CogneeError, match="list"):
        cognee_client.recall("q")


def test_an_empty_200_body_is_an_empty_dict_for_a_delete(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    assert cognee_client.delete_dataset(DATASET_ID) == {}


# --- error hygiene -------------------------------------------------------------------------


def test_an_error_names_the_status_and_a_short_body_excerpt(cognee_server: FakeCognee, cognee_client: CogneeClient) -> None:
    cognee_server.fail_next(422, "x" * 2000)

    with pytest.raises(CogneeError) as err:
        cognee_client.recall("q")

    assert err.value.status == 422
    assert "422" in str(err.value) and "/api/v1/recall" in str(err.value)
    assert 0 < len(err.value.body_excerpt) <= 200
    assert len(str(err.value)) < 400


def test_secrets_never_appear_in_errors_even_when_the_server_echoes_them(
    cognee_server: FakeCognee, caplog: pytest.LogCaptureFixture
) -> None:
    cognee_server.fail_next(500, f"upstream rejected key {API_KEY} for tenant {TENANT_ID}", times=2)
    cognee_server.time_out_next(times=2)

    with caplog.at_level(logging.DEBUG), make_client() as client:
        with pytest.raises(CogneeError) as server_error:
            client.recall("q")
        with pytest.raises(CogneeError) as timeout_error:
            client.recall("q")
    wrong = make_client(api_key=Secret("wrong-key-987654"))
    with pytest.raises(CogneeError) as auth_error:
        wrong.recall("q")  # the fake echoes the key it was sent in its 401
    wrong.close()

    for error in (server_error.value, timeout_error.value, auth_error.value):
        shown = f"{error} {error!r} {error.body_excerpt} {error.__cause__!r}"
        assert API_KEY not in shown and TENANT_ID not in shown and "wrong-key-987654" not in shown
    assert auth_error.value.status == 401
    logged = " ".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert API_KEY not in logged and TENANT_ID not in logged and "wrong-key-987654" not in logged


def test_successful_calls_do_not_log_request_bodies(cognee_server: FakeCognee, cognee_client: CogneeClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        cognee_client.remember_text("traveler is allergic to peanuts", session_id="s")

    assert "peanuts" not in " ".join(r.getMessage() for r in caplog.records)
