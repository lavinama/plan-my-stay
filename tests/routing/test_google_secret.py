"""The Google key must never reach an exception, a log record or a repr."""

import logging
import traceback

import httpx
import pytest

from plan_my_stay.config import Secret
from plan_my_stay.enums import Mode
from plan_my_stay.routing.google import GEOCODE_URL, MATRIX_URL, GoogleMapsClient
from plan_my_stay.routing.maps import MapsError

from .google_support import A, ALL, B, DEPART, GEOCODE_OK, KEY, element


def fail_text(exc: BaseException) -> str:
    """Everything an exception could show someone: message, repr, cause chain and the printed traceback."""
    return " | ".join([str(exc), repr(exc), "".join(traceback.format_exception(exc))])


def failures(router, client):
    """Provoke every kind of failure and yield the MapsError each raised."""
    cases = [
        (GEOCODE_URL, "get", httpx.Response(200, json={"status": "REQUEST_DENIED", "error_message": f"bad key {KEY}"}),
         lambda: client.geocode("x")),
        (GEOCODE_URL, "get", httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []}), lambda: client.geocode("x")),
        (GEOCODE_URL, "get", httpx.Response(500, text=f"oops {KEY}"), lambda: client.geocode("x")),
        (GEOCODE_URL, "get", httpx.ConnectError("connect failed for " + GEOCODE_URL + "?key=" + KEY), lambda: client.geocode("x")),
        (GEOCODE_URL, "get", httpx.ReadTimeout("timeout"), lambda: client.geocode("x")),
        (MATRIX_URL, "post", httpx.Response(403, json={"error": {"message": f"key {KEY} blocked"}}),
         lambda: client.route_minutes(A, B, DEPART, [Mode.DRIVING])),
        (MATRIX_URL, "post", httpx.Response(429, json={"error": {"message": KEY}}),
         lambda: client.route_minutes(A, B, DEPART, [Mode.DRIVING])),
        (MATRIX_URL, "post", httpx.Response(200, text=f"not json {KEY}"), lambda: client.route_minutes(A, B, DEPART, [Mode.WALKING])),
        (MATRIX_URL, "post", httpx.ConnectError("x"), lambda: client.route_minutes(A, B, DEPART, [Mode.TRANSIT])),
    ]
    for url, method, outcome, call in cases:
        route = router.route(method=method.upper(), url=url)
        if isinstance(outcome, Exception):
            route.mock(side_effect=outcome)
        else:
            route.mock(return_value=outcome)
        try:
            call()
        except MapsError as exc:
            yield exc
        else:
            raise AssertionError(f"{url} did not fail")


def test_the_key_never_appears_in_any_exception_text(router, client):
    errors = list(failures(router, client))
    assert len(errors) == 9
    for exc in errors:
        assert KEY not in fail_text(exc)
        assert exc.__cause__ is None  # the underlying httpx error (whose text can hold the URL) is not chained


def test_the_key_never_appears_in_a_log_record_even_httpxs_own_request_line(router, client, caplog):
    router.get(GEOCODE_URL).respond(200, json=GEOCODE_OK)
    router.post(MATRIX_URL).respond(200, json=[element()])
    with caplog.at_level(logging.DEBUG):
        client.geocode("199 Water St, New York, NY 10038")
        client.route_minutes(A, B, DEPART, ALL)
        list(failures(router, client))

    texts = [record.getMessage() for record in caplog.records]
    assert any(r.name == "httpx" and "HTTP Request" in r.getMessage() for r in caplog.records)  # the check is not vacuous
    assert any("key=***" in t for t in texts)  # httpx logs the geocode URL, and the key was scrubbed from it
    assert not [t for t in texts if KEY in t]
    assert not [r for r in caplog.records if KEY in repr(r.__dict__)]


def test_the_key_is_not_in_the_clients_repr_or_attributes(client):
    assert KEY not in repr(client) and KEY not in str(client)
    assert KEY not in repr(vars(client))


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_key_is_refused_at_construction(empty):
    with pytest.raises(ValueError, match="api_key"):
        GoogleMapsClient(Secret(empty))


def test_a_plain_string_key_is_refused_so_it_is_always_wrapped():
    with pytest.raises(TypeError, match="Secret"):
        GoogleMapsClient(KEY)  # type: ignore[arg-type]


def test_close_closes_only_a_client_it_created(router):
    borrowed = httpx.Client()
    GoogleMapsClient(Secret(KEY), http=borrowed).close()
    assert not borrowed.is_closed

    router.get(GEOCODE_URL).respond(200, json=GEOCODE_OK)
    owner = GoogleMapsClient(Secret(KEY))
    assert owner.geocode("x").lat == 40.7066
    owner.close()


def test_a_negative_call_cap_and_a_naive_departure_are_refused(client):
    with pytest.raises(ValueError, match="max_calls"):
        GoogleMapsClient(Secret(KEY), max_calls=-1)
    with pytest.raises(ValueError, match="offset"):
        client.route_minutes(A, B, DEPART.replace(tzinfo=None), [Mode.WALKING])


def test_the_redaction_filter_leaves_a_malformed_record_for_logging_to_report():
    from plan_my_stay.routing.google import _KeyRedactingFilter

    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "needs %s and %s", ("one",), None)
    assert _KeyRedactingFilter().filter(record) is True
    assert record.msg == "needs %s and %s"


def test_the_filter_is_installed_once_however_many_clients_exist():
    for _ in range(3):
        GoogleMapsClient(Secret(KEY))
    from plan_my_stay.routing.google import _KeyRedactingFilter

    assert sum(isinstance(f, _KeyRedactingFilter) for f in logging.getLogger("httpx").filters) == 1
