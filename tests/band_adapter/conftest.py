"""Shared fixtures: a directory with the six default display names, and tiny message builders."""

import copy
import json
from typing import Any

import pytest

from plan_my_stay.band_adapter.directory import SeatDirectory
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import Message, build_message, parse_message
from plan_my_stay.messages.examples import valid_examples

NAMES: dict[Seat, str] = {
    Seat.CONCIERGE: "Plan My Stay Concierge",
    Seat.STAY: "Plan My Stay Stay Finder",
    Seat.RESTAURANT: "Plan My Stay Restaurant Scout",
    Seat.GYM: "Plan My Stay Gym Scout",
    Seat.VERIFIER: "Plan My Stay Verifier",
    Seat.ROUTING: "Plan My Stay Routing",
}


@pytest.fixture
def directory() -> SeatDirectory:
    return SeatDirectory(NAMES)


def example_raw(name: str, **overrides: Any) -> dict[str, Any]:
    """A deep copy of one valid example envelope with top-level overrides."""
    return {**copy.deepcopy(valid_examples()[name]), **overrides}


def example_body(name: str, **overrides: Any) -> str:
    return json.dumps(example_raw(name, **overrides))


def route_request_for_routing() -> Message:
    """A valid message addressed to the Routing seat (from Stay), the usual incoming message in these tests."""
    return parse_message(example_body("route_request"))


def route_reply_to(request: Message) -> Message:
    """The valid answer Routing gives to `request`."""
    return build_message(
        type=MessageType.ROUTE_REPLY,
        sender=Seat.ROUTING,
        to=[request.sender],
        trip_id=request.trip_id,
        user_id=request.user_id,
        state_version=request.state_version,
        summary="Routing reply",
        reply_to=request.message_id,
        payload=valid_examples()["route_reply"]["payload"],
    )
