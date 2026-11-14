"""Routing also geocodes: an empty `modes` list is a geocode-only lookup, answered with resolved coordinates."""

import pytest

from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import MessageParseError, build_message, parse_message, serialize
from plan_my_stay.demo import TRIP_ID, USER_ID, at


def _route(type_, sender, to, payload, **kw):
    return build_message(type=type_, sender=sender, to=to, trip_id=TRIP_ID, user_id=USER_ID, state_version=0,
                         summary="route", payload=payload, **kw)


def test_geocode_only_request_has_no_modes():
    msg = _route(
        MessageType.ROUTE_REQUEST, Seat.CONCIERGE, [Seat.ROUTING],
        {"origin": {"address": "199 Water St, New York, NY 10038"}, "destination": {"address": "199 Water St, New York, NY 10038"},
         "depart_at": at(17, 11), "modes": []},
    )
    assert msg.payload.modes == ()
    assert parse_message(serialize(msg)) == msg


def test_geocode_only_reply_carries_resolved_coordinates_without_minutes():
    msg = _route(
        MessageType.ROUTE_REPLY, Seat.ROUTING, [Seat.CONCIERGE],
        {"origin": {"address": "199 Water St", "lat": 40.7066, "lng": -74.0053}},
    )
    assert msg.payload.origin.lat == 40.7066 and msg.payload.minutes == {}


def test_a_reply_with_no_minutes_no_error_and_nothing_resolved_is_rejected():
    with pytest.raises(MessageParseError):
        _route(MessageType.ROUTE_REPLY, Seat.ROUTING, [Seat.CONCIERGE], {})


def test_an_error_reply_needs_no_minutes():
    msg = _route(MessageType.ROUTE_REPLY, Seat.ROUTING, [Seat.CONCIERGE], {"error": "geocode_failed"})
    assert msg.payload.error == "geocode_failed"
