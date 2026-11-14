"""GoogleMapsClient against respx: request shapes, parsing, errors and the call cap."""

import json
from datetime import timedelta

import httpx
import pytest

from plan_my_stay.config import Secret
from plan_my_stay.enums import Mode
from plan_my_stay.routing.google import GEOCODE_URL, MATRIX_URL, GoogleMapsClient
from plan_my_stay.routing.maps import GeocodeResult, MapsClient, MapsError

from .google_support import A, ALL, B, DEPART, GEOCODE_OK, KEY, NOW, element


def test_it_is_a_maps_client(client):
    assert isinstance(client, MapsClient)


# --- geocoding ---------------------------------------------------------------------------------


def test_geocode_sends_a_get_with_the_address_and_key_and_parses_the_first_result(router, client):
    route = router.get(GEOCODE_URL).respond(200, json=GEOCODE_OK)
    result = client.geocode("199 Water St, New York, NY 10038")

    assert result == GeocodeResult(lat=40.7066, lng=-74.0053, formatted_address="199 Water St, New York, NY 10038, USA")
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.params["address"] == "199 Water St, New York, NY 10038"
    assert request.url.params["key"] == KEY


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("ZERO_RESULTS", "geocode_failed"),
        ("OVER_QUERY_LIMIT", "quota_exceeded"),
        ("OVER_DAILY_LIMIT", "quota_exceeded"),
        ("REQUEST_DENIED", "upstream_error"),
        ("INVALID_REQUEST", "upstream_error"),
        ("UNKNOWN_ERROR", "upstream_error"),
        ("SOMETHING_NEW", "upstream_error"),
    ],
)
def test_geocode_status_maps_to_an_error_code(router, client, status, code):
    router.get(GEOCODE_URL).respond(200, json={"status": status, "results": [], "error_message": "details"})
    with pytest.raises(MapsError) as err:
        client.geocode("nowhere")
    assert err.value.code == code


def test_a_blank_address_fails_without_a_request_or_a_counted_call(router, client):
    route = router.get(GEOCODE_URL).respond(200, json=GEOCODE_OK)
    with pytest.raises(MapsError) as err:
        client.geocode("   ")
    assert err.value.code == "geocode_failed"
    assert not route.called and client.calls_made == 0


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"status": "OK", "results": []}),
        httpx.Response(200, json={"status": "OK", "results": [{"formatted_address": "x"}]}),
        httpx.Response(200, json={"status": "OK", "results": [{"geometry": {"location": {"lat": 123.0, "lng": 1.0}}}]}),
        httpx.Response(200, json={"status": "OK", "results": [{"geometry": {"location": {"lat": "x", "lng": 1.0}}}]}),
    ],
)
def test_geocode_garbage_is_an_upstream_error(router, client, response):
    router.get(GEOCODE_URL).mock(return_value=response)
    with pytest.raises(MapsError) as err:
        client.geocode("199 Water St")
    assert err.value.code == "upstream_error"


def test_geocode_http_429_is_quota_exceeded(router, client):
    router.get(GEOCODE_URL).respond(429, json={"error": "rate"})
    with pytest.raises(MapsError) as err:
        client.geocode("199 Water St")
    assert err.value.code == "quota_exceeded"


@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("eof")])
def test_network_failures_are_upstream_errors(router, client, exc):
    router.get(GEOCODE_URL).mock(side_effect=exc)
    with pytest.raises(MapsError) as err:
        client.geocode("199 Water St")
    assert err.value.code == "upstream_error"


# --- routes ------------------------------------------------------------------------------------


def test_route_minutes_sends_one_matrix_request_per_mode_with_the_documented_shape(router, client):
    route = router.post(MATRIX_URL).respond(200, json=[element("1260s")])
    client.route_minutes(A, B, DEPART, ALL)

    assert route.call_count == 3
    bodies = {json.loads(call.request.content)["travelMode"]: json.loads(call.request.content) for call in route.calls}
    assert set(bodies) == {"WALK", "TRANSIT", "DRIVE"}
    for body in bodies.values():
        assert body["origins"] == [{"waypoint": {"location": {"latLng": {"latitude": 40.7066, "longitude": -74.0053}}}}]
        assert body["destinations"] == [{"waypoint": {"location": {"latLng": {"latitude": 40.7502, "longitude": -73.9987}}}}]


def test_headers_carry_the_key_and_a_field_mask_but_the_url_does_not_carry_the_key(router, client):
    route = router.post(MATRIX_URL).respond(200, json=[element()])
    client.route_minutes(A, B, DEPART, [Mode.WALKING])
    request = route.calls.last.request

    assert str(request.url) == MATRIX_URL
    assert request.headers["X-Goog-Api-Key"] == KEY
    assert request.headers["X-Goog-FieldMask"] == "originIndex,destinationIndex,duration,status,condition"
    assert request.headers["Content-Type"] == "application/json"
    assert KEY not in str(request.url)


def test_departure_time_is_rfc3339_with_the_new_york_offset_for_transit_and_driving(router, client):
    route = router.post(MATRIX_URL).respond(200, json=[element()])
    client.route_minutes(A, B, DEPART, ALL)
    bodies = {json.loads(c.request.content)["travelMode"]: json.loads(c.request.content) for c in route.calls}

    assert bodies["TRANSIT"]["departureTime"] == "2026-11-17T16:00:00-05:00"
    assert bodies["DRIVE"]["departureTime"] == "2026-11-17T16:00:00-05:00"
    assert "departureTime" not in bodies["WALK"]  # walking time does not depend on the hour


def test_only_driving_asks_for_traffic_aware_routing(router, client):
    route = router.post(MATRIX_URL).respond(200, json=[element()])
    client.route_minutes(A, B, DEPART, ALL)
    bodies = {json.loads(c.request.content)["travelMode"]: json.loads(c.request.content) for c in route.calls}

    assert bodies["DRIVE"]["routingPreference"] == "TRAFFIC_AWARE"
    assert "routingPreference" not in bodies["TRANSIT"]  # Google fails the request otherwise
    assert "routingPreference" not in bodies["WALK"]
    assert "arrivalTime" not in bodies["TRANSIT"]


def test_a_past_departure_is_sent_for_transit_but_left_out_for_driving(router):
    client = GoogleMapsClient(Secret(KEY), http=httpx.Client(), clock=lambda: DEPART + timedelta(hours=1))
    route = router.post(MATRIX_URL).respond(200, json=[element()])
    client.route_minutes(A, B, DEPART, [Mode.TRANSIT, Mode.DRIVING])
    bodies = {json.loads(c.request.content)["travelMode"]: json.loads(c.request.content) for c in route.calls}

    assert bodies["TRANSIT"]["departureTime"] == "2026-11-17T16:00:00-05:00"  # Google allows the past for transit only
    assert "departureTime" not in bodies["DRIVE"]  # it would be rejected; Google then uses the request time


def test_durations_become_minutes_with_one_decimal(router, client):
    durations = {"WALK": "3672s", "TRANSIT": "725s", "DRIVE": "540s"}

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[element(durations[json.loads(request.content)["travelMode"]])])

    router.post(MATRIX_URL).mock(side_effect=respond)
    minutes = client.route_minutes(A, B, DEPART, ALL)
    assert minutes == {Mode.WALKING: 61.2, Mode.TRANSIT: 12.1, Mode.DRIVING: 9.0}
    assert list(minutes) == list(ALL)


@pytest.mark.parametrize(("duration", "expected"), [("90.5s", 1.5), ("0s", 0.0), ("1260s", 21.0)])
def test_fractional_and_zero_durations(router, client, duration, expected):
    router.post(MATRIX_URL).respond(200, json=[element(duration)])
    assert client.route_minutes(A, B, DEPART, [Mode.DRIVING]) == {Mode.DRIVING: expected}


def test_index_zero_may_be_omitted_by_proto_json_and_is_still_found(router, client):
    router.post(MATRIX_URL).respond(200, json=[element("600s")])  # no originIndex / destinationIndex at all
    assert client.route_minutes(A, B, DEPART, [Mode.WALKING]) == {Mode.WALKING: 10.0}
    router.post(MATRIX_URL).respond(200, json=[element("600s", originIndex=0, destinationIndex=0)])
    assert client.route_minutes(A, B, DEPART, [Mode.WALKING]) == {Mode.WALKING: 10.0}


def test_route_not_found_for_one_mode_is_none_and_the_others_still_answer(router, client):
    def respond(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["travelMode"] == "TRANSIT":
            return httpx.Response(200, json=[element(None, "ROUTE_NOT_FOUND", status={"code": 5})])
        return httpx.Response(200, json=[element("600s")])

    router.post(MATRIX_URL).mock(side_effect=respond)
    assert client.route_minutes(A, B, DEPART, ALL) == {Mode.WALKING: 10.0, Mode.TRANSIT: None, Mode.DRIVING: 10.0}


@pytest.mark.parametrize(
    ("status", "code"),
    [({"code": 8, "message": "quota"}, "quota_exceeded"), ({"code": 13, "message": "internal"}, "upstream_error")],
)
def test_an_element_level_error_status_maps_to_a_code(router, client, status, code):
    router.post(MATRIX_URL).respond(200, json=[{"status": status, "condition": "ROUTE_MATRIX_ELEMENT_CONDITION_UNSPECIFIED"}])
    with pytest.raises(MapsError) as err:
        client.route_minutes(A, B, DEPART, [Mode.WALKING])
    assert err.value.code == code


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"error": {"code": 400}},
        "nope",
        [element(None)],  # ROUTE_EXISTS but no duration
        [element("soon")],
        [element("-5s")],
        [element("600s", originIndex=3)],  # not the one element we asked for
        [{"condition": "SOMETHING_NEW", "status": {}}],
    ],
)
def test_malformed_matrix_responses_are_upstream_errors(router, client, body):
    router.post(MATRIX_URL).respond(200, json=body)
    with pytest.raises(MapsError) as err:
        client.route_minutes(A, B, DEPART, [Mode.WALKING])
    assert err.value.code == "upstream_error"


@pytest.mark.parametrize(("http_status", "code"), [(429, "quota_exceeded"), (400, "upstream_error"), (403, "upstream_error"), (503, "upstream_error")])
def test_matrix_http_errors(router, client, http_status, code):
    router.post(MATRIX_URL).respond(http_status, json={"error": {"code": http_status, "status": "X"}})
    with pytest.raises(MapsError) as err:
        client.route_minutes(A, B, DEPART, [Mode.DRIVING])
    assert err.value.code == code


def test_matrix_network_failure_is_an_upstream_error(router, client):
    router.post(MATRIX_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(MapsError) as err:
        client.route_minutes(A, B, DEPART, [Mode.DRIVING])
    assert err.value.code == "upstream_error"


def test_no_modes_means_no_requests_and_duplicate_modes_one_request(router, client):
    route = router.post(MATRIX_URL).respond(200, json=[element()])
    assert client.route_minutes(A, B, DEPART, []) == {}
    assert not route.called
    assert client.route_minutes(A, B, DEPART, [Mode.WALKING, Mode.WALKING]) == {Mode.WALKING: 21.0}
    assert route.call_count == 1


# --- quota -------------------------------------------------------------------------------------


def test_max_calls_counts_every_google_request_and_refuses_beyond_it(router):
    geocode = router.get(GEOCODE_URL).respond(200, json=GEOCODE_OK)
    matrix = router.post(MATRIX_URL).respond(200, json=[element()])
    client = GoogleMapsClient(Secret(KEY), http=httpx.Client(), max_calls=3, clock=lambda: NOW)

    client.geocode("199 Water St")  # 1
    client.route_minutes(A, B, DEPART, [Mode.WALKING, Mode.DRIVING])  # 2 and 3
    assert client.calls_made == 3
    with pytest.raises(MapsError) as err:
        client.geocode("368 9th Ave")
    assert err.value.code == "quota_exceeded"
    assert geocode.call_count == 1 and matrix.call_count == 2  # nothing was sent for the refused call
    assert client.calls_made == 3


def test_a_matrix_that_would_cross_the_cap_stops_at_the_cap(router):
    matrix = router.post(MATRIX_URL).respond(200, json=[element()])
    client = GoogleMapsClient(Secret(KEY), http=httpx.Client(), max_calls=2, clock=lambda: NOW)
    with pytest.raises(MapsError) as err:
        client.route_minutes(A, B, DEPART, ALL)  # three modes, two calls left
    assert err.value.code == "quota_exceeded"
    assert matrix.call_count == 2


def test_max_calls_zero_refuses_everything_and_none_means_unlimited(router):
    router.get(GEOCODE_URL).respond(200, json=GEOCODE_OK)
    with pytest.raises(MapsError):
        GoogleMapsClient(Secret(KEY), http=httpx.Client(), max_calls=0).geocode("x")
    unlimited = GoogleMapsClient(Secret(KEY), http=httpx.Client())
    for _ in range(5):
        unlimited.geocode("x")
    assert unlimited.calls_made == 5


def test_a_failed_request_still_counts_as_a_call(router):
    router.get(GEOCODE_URL).respond(500)
    client = GoogleMapsClient(Secret(KEY), http=httpx.Client(), max_calls=1)
    with pytest.raises(MapsError):
        client.geocode("x")
    with pytest.raises(MapsError) as err:
        client.geocode("x")
    assert err.value.code == "quota_exceeded"
