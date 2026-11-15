import json

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Mode, Seat
from plan_my_stay.messages import Message, build_message, parse_message, serialize
from plan_my_stay.models import Place
from plan_my_stay.routing.cache import RouteCache
from plan_my_stay.routing.fixture import FixtureMapsClient
from plan_my_stay.routing.seat import RoutingSeat
from plan_my_stay.seat import Seat as SeatProtocol

from .fakes import FakeMapsClient

ALL = [Mode.WALKING, Mode.TRANSIT, Mode.DRIVING]
ADDR_A = "199 Water St, New York, NY 10038"
ADDR_B = "368 9th Ave, New York, NY 10001"


def request(origin: dict, destination: dict, modes=ALL, sender: Seat = Seat.CONCIERGE, version: int = 3) -> Message:
    return build_message(
        type=MessageType.ROUTE_REQUEST, sender=sender, to=[Seat.ROUTING], trip_id=demo.TRIP_ID, user_id=demo.USER_ID,
        state_version=version, summary="Route request",
        payload={"origin": origin, "destination": destination, "depart_at": demo.at(17, 12), "modes": modes},
    )


def only(replies) -> Message:
    assert len(replies) == 1
    reply = replies[0]
    assert isinstance(reply, Message)
    assert parse_message(serialize(reply)) == reply  # valid on the wire, not just in memory
    return reply


def test_it_is_a_seat():
    assert isinstance(RoutingSeat(FakeMapsClient()), SeatProtocol)


def test_route_request_with_addresses_gets_minutes_and_resolved_places():
    maps = FakeMapsClient(locations={ADDR_A: (40.7066, -74.0053), ADDR_B: (40.7502, -73.9987)})
    req = request({"address": ADDR_A}, {"address": ADDR_B})
    reply = only(RoutingSeat(maps).handle(req))

    assert reply.type is MessageType.ROUTE_REPLY
    assert reply.sender is Seat.ROUTING and reply.to == (Seat.CONCIERGE,)
    assert reply.reply_to == req.message_id
    assert (reply.trip_id, reply.user_id, reply.state_version) == (demo.TRIP_ID, demo.USER_ID, 3)
    assert reply.payload.error is None
    assert reply.payload.minutes == {Mode.WALKING: 21.0, Mode.TRANSIT: 12.0, Mode.DRIVING: 9.0}
    assert reply.payload.origin == Place(address=ADDR_A, lat=40.7066, lng=-74.0053)
    assert reply.payload.destination == Place(address=ADDR_B, lat=40.7502, lng=-73.9987)
    assert reply.summary == "Route: 21 min walking, 12 transit, 9 driving"


def test_the_reply_goes_to_whichever_seat_asked():
    reply = only(RoutingSeat(FakeMapsClient()).handle(request({"address": ADDR_A}, {"address": ADDR_B}, sender=Seat.STAY)))
    assert reply.to == (Seat.STAY,)


def test_coordinates_are_used_as_given_and_never_geocoded():
    maps = FakeMapsClient()
    reply = only(RoutingSeat(maps).handle(request({"lat": 40.7066, "lng": -74.0053}, {"lat": 40.7502, "lng": -73.9987})))
    assert maps.geocode_calls == []
    assert reply.payload.origin == Place(lat=40.7066, lng=-74.0053)
    assert reply.payload.minutes[Mode.WALKING] == 21.0


def test_a_place_with_both_address_and_coordinates_keeps_its_coordinates():
    maps = FakeMapsClient()
    only(RoutingSeat(maps).handle(request({"address": ADDR_A, "lat": 40.7066, "lng": -74.0053}, {"lat": 40.7502, "lng": -73.9987})))
    assert maps.geocode_calls == []


def test_only_the_requested_modes_are_asked_for_and_answered():
    maps = FakeMapsClient()
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["driving"])))
    assert maps.route_calls[0][3] == (Mode.DRIVING,)
    assert reply.payload.minutes == {Mode.DRIVING: 9.0}
    assert reply.summary == "Route: 9 min driving"


def test_a_second_identical_request_is_served_from_the_cache():
    maps = FakeMapsClient()
    seat = RoutingSeat(maps)
    first = only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    second = only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B})))

    assert len(maps.geocode_calls) == 2  # once per distinct address, not once per request
    assert len(maps.route_calls) == 1
    assert second.payload.minutes == first.payload.minutes
    assert second.payload.origin == first.payload.origin
    assert second.message_id != first.message_id


def test_a_later_request_in_the_same_hour_hits_but_another_mode_set_asks_only_for_the_missing_ones():
    maps = FakeMapsClient()
    seat = RoutingSeat(maps)
    only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["walking"])))
    reply = only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["walking", "driving"])))
    assert [call[3] for call in maps.route_calls] == [(Mode.WALKING,), (Mode.DRIVING,)]
    assert reply.payload.minutes == {Mode.WALKING: 21.0, Mode.DRIVING: 9.0}


def test_a_shared_cache_serves_a_second_seat():
    cache = RouteCache()
    RoutingSeat(FakeMapsClient(), cache).handle(request({"address": ADDR_A}, {"address": ADDR_B}))
    maps = FakeMapsClient()
    reply = only(RoutingSeat(maps, cache).handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    assert maps.geocode_calls == [] and maps.route_calls == []
    assert reply.payload.minutes[Mode.TRANSIT] == 12.0


def test_geocode_only_lookup_resolves_the_place_and_sends_no_minutes_or_route_call():
    maps = FakeMapsClient(locations={ADDR_A: (40.7066, -74.0053)})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_A}, modes=[])))
    assert maps.route_calls == []
    assert maps.geocode_calls == [ADDR_A]  # origin and destination are the same address: one lookup
    assert reply.payload.minutes == {} and reply.payload.error is None
    assert reply.payload.origin == reply.payload.destination == Place(address=ADDR_A, lat=40.7066, lng=-74.0053)
    assert ADDR_A in reply.summary


def test_geocode_failure_becomes_an_error_reply_that_echoes_what_resolved():
    maps = FakeMapsClient(locations={ADDR_A: (40.7066, -74.0053)}, geocode_errors={"nowhere": "geocode_failed"})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": "nowhere"})))
    assert reply.type is MessageType.ROUTE_REPLY
    assert reply.payload.error == "geocode_failed"
    assert reply.payload.minutes == {}
    assert reply.payload.origin == Place(address=ADDR_A, lat=40.7066, lng=-74.0053)
    assert reply.payload.destination == Place(address="nowhere")  # no coordinates: this is the one that failed
    assert maps.route_calls == []
    assert "geocode_failed" in reply.summary
    assert reply.reply_to is not None


def test_no_route_for_one_mode_is_null_for_that_mode():
    maps = FakeMapsClient(minutes={Mode.WALKING: 61.2, Mode.TRANSIT: None, Mode.DRIVING: 19.3})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    assert reply.payload.error is None
    assert reply.payload.minutes == {Mode.WALKING: 61.2, Mode.TRANSIT: None, Mode.DRIVING: 19.3}
    assert reply.summary == "Route: 61.2 min walking, no transit route, 19.3 driving"
    assert json.loads(serialize(reply))["payload"]["minutes"]["transit"] is None


def test_a_cached_no_route_is_not_asked_again():
    maps = FakeMapsClient(minutes={Mode.TRANSIT: None})
    seat = RoutingSeat(maps)
    only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["transit"])))
    reply = only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["transit"])))
    assert len(maps.route_calls) == 1
    assert reply.payload.minutes == {Mode.TRANSIT: None}


def test_no_route_in_any_mode_is_reported_as_no_route():
    maps = FakeMapsClient(minutes={Mode.WALKING: None, Mode.TRANSIT: None, Mode.DRIVING: None})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    assert reply.payload.error == "no_route"
    assert reply.payload.minutes == {Mode.WALKING: None, Mode.TRANSIT: None, Mode.DRIVING: None}
    assert reply.payload.origin is not None


@pytest.mark.parametrize("code", ["quota_exceeded", "upstream_error"])
def test_a_maps_error_on_the_route_call_becomes_the_reply_error(code):
    maps = FakeMapsClient(route_error=code)
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    assert reply.payload.error == code
    assert reply.payload.minutes == {}
    assert reply.payload.origin is not None  # geocoding worked, so say where things are


def test_quota_exceeded_on_geocode_is_reported_too():
    maps = FakeMapsClient(geocode_errors={ADDR_A: "quota_exceeded"})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    assert reply.payload.error == "quota_exceeded"
    assert reply.payload.origin == Place(address=ADDR_A)


def test_a_failed_lookup_is_not_cached_so_a_retry_can_succeed():
    maps = FakeMapsClient(route_error="upstream_error")
    seat = RoutingSeat(maps)
    assert only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B}))).payload.error == "upstream_error"
    maps.route_error = None
    assert only(seat.handle(request({"address": ADDR_A}, {"address": ADDR_B}))).payload.error is None


@pytest.mark.parametrize("sender", [Seat.CONCIERGE, Seat.STAY])
def test_any_other_message_type_gets_an_unexpected_type_error(sender):
    other = build_message(
        type=MessageType.PREFERENCE_LEARNED, sender=sender, to=[Seat.ROUTING], trip_id=demo.TRIP_ID, user_id=demo.USER_ID, state_version=5, summary="pref",
        payload={"key": "diet", "value": "vegetarian", "evidence": "said so"},
    )
    reply = only(RoutingSeat(FakeMapsClient()).handle(other))
    assert reply.type is MessageType.ERROR
    assert reply.sender is Seat.ROUTING and reply.to == (other.sender,)
    assert reply.reply_to == other.message_id
    assert reply.payload.code == "unexpected_type"
    assert reply.payload.bad_message_id == other.message_id
    assert (reply.trip_id, reply.user_id, reply.state_version) == (demo.TRIP_ID, demo.USER_ID, 5)


def test_an_error_message_gets_no_answer_so_two_seats_cannot_ping_pong():
    err = build_message(
        type=MessageType.ERROR, sender=Seat.CONCIERGE, to=[Seat.ROUTING], trip_id=demo.TRIP_ID, user_id=demo.USER_ID,
        state_version=1, summary="Rejected a message", payload={"code": "validation_failed"},
    )
    assert RoutingSeat(FakeMapsClient()).handle(err) == []


def test_it_works_end_to_end_with_the_fixture_client_for_the_demo_trip():
    seat = RoutingSeat(FixtureMapsClient())
    geo = {
        key: only(seat.handle(request({"address": addr}, {"address": addr}, modes=[]))).payload.origin
        for key, addr in demo.MEETING_ADDRESSES.items()
    }
    anchors = {a.id: a for a in demo.demo_anchors()}
    for key, place in geo.items():
        assert (place.lat, place.lng) == (anchors[key].lat, anchors[key].lng)

    reply = only(seat.handle(request(geo["A"].model_dump(), geo["B"].model_dump())))
    assert reply.payload.minutes[Mode.DRIVING] < reply.payload.minutes[Mode.WALKING]
    assert reply.summary.startswith("Route: ")


def test_a_blank_address_is_geocode_failed_without_asking_the_provider():
    maps = FakeMapsClient()
    reply = only(RoutingSeat(maps).handle(request({"address": "  "}, {"address": ADDR_B})))
    assert reply.payload.error == "geocode_failed"
    assert maps.geocode_calls == [] and maps.route_calls == []


def test_geocode_only_for_two_different_places_resolves_both():
    maps = FakeMapsClient(locations={ADDR_A: (40.7066, -74.0053), ADDR_B: (40.7502, -73.9987)})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=[])))
    assert reply.payload.origin.lat == 40.7066 and reply.payload.destination.lat == 40.7502
    assert reply.summary == "Located the origin and the destination"


def test_duplicate_modes_are_asked_for_once_and_answered_once():
    maps = FakeMapsClient()
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["walking", "walking"])))
    assert maps.route_calls[0][3] == (Mode.WALKING,)
    assert reply.payload.minutes == {Mode.WALKING: 21.0}


def test_summary_puts_the_unit_on_the_first_number_even_when_an_earlier_mode_has_no_route():
    maps = FakeMapsClient(minutes={Mode.WALKING: None, Mode.TRANSIT: 12.0, Mode.DRIVING: 9.0})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    assert reply.summary == "Route: no walking route, 12 min transit, 9 driving"


def test_summary_for_no_route_at_all():
    maps = FakeMapsClient(minutes={Mode.WALKING: None, Mode.DRIVING: None})
    reply = only(RoutingSeat(maps).handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["walking", "driving"])))
    assert reply.summary == "Route: no route by walking, driving"


class SkippingClient(FakeMapsClient):
    """A broken provider client that leaves a requested mode out of its answer."""

    def route_minutes(self, origin, destination, depart_at, modes):
        return {mode: value for mode, value in super().route_minutes(origin, destination, depart_at, modes).items() if mode is not Mode.TRANSIT}


@pytest.mark.parametrize("bad", [-3.0, float("nan"), float("inf")])
def test_an_impossible_time_from_the_provider_is_an_upstream_error_and_is_not_cached(bad):
    cache = RouteCache()
    maps = FakeMapsClient(minutes={Mode.WALKING: bad})
    reply = only(RoutingSeat(maps, cache).handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["walking"])))
    assert reply.payload.error == "upstream_error" and reply.payload.minutes == {}

    retry = only(RoutingSeat(FakeMapsClient(), cache).handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["walking"])))
    assert retry.payload.minutes == {Mode.WALKING: 21.0}  # the bad value was not cached


def test_a_mode_the_provider_client_forgot_to_answer_is_an_upstream_error_and_is_not_cached():
    cache = RouteCache()
    reply = only(RoutingSeat(SkippingClient(), cache).handle(request({"address": ADDR_A}, {"address": ADDR_B})))
    assert reply.payload.error == "upstream_error"
    fixed = only(RoutingSeat(FakeMapsClient(), cache).handle(request({"address": ADDR_A}, {"address": ADDR_B}, modes=["transit"])))
    assert fixed.payload.minutes == {Mode.TRANSIT: 12.0}  # nothing stale was cached for transit
