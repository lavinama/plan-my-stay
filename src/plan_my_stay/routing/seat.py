"""The Routing seat: plain code, no LLM. It answers `route_request` and nothing else.

Any seat may ask. Routing resolves each place (geocoding an address unless coordinates came with
it), asks the map provider only for what is not cached, and answers with one `route_reply` to the
requester. A provider failure becomes `route_reply.error`; a message of any other type gets one
`error` reply (`unexpected_type`), so no turn is ever silent. Only an incoming `error` is left
unanswered, because answering an error with an error lets two seats talk forever.
"""

import logging
import math
from collections.abc import Sequence

from plan_my_stay.enums import MessageType, Mode, Seat
from plan_my_stay.messages import Message, build_message
from plan_my_stay.messages.payloads import ErrorPayload, RouteReplyPayload, RouteRequestPayload
from plan_my_stay.models import LatLng, Place
from plan_my_stay.routing.cache import RouteCache
from plan_my_stay.routing.maps import MapsClient, MapsError
from plan_my_stay.seat import Outgoing

logger = logging.getLogger(__name__)

UNEXPECTED_TYPE = "unexpected_type"
_SUMMARY_ADDRESS_LIMIT = 120


class RoutingSeat:
    def __init__(self, maps: MapsClient, cache: RouteCache | None = None) -> None:
        self._maps = maps
        self._cache = cache if cache is not None else RouteCache()

    def handle(self, message: Message) -> Sequence[Outgoing]:
        if message.type is MessageType.ERROR:
            logger.info("Ignoring an error message from %s (no answer, so errors cannot ping-pong)", message.sender.value)
            return []
        if message.type is not MessageType.ROUTE_REQUEST:
            return [self._unexpected_type(message)]
        payload = message.payload
        assert isinstance(payload, RouteRequestPayload)  # Message checks the payload against its type
        return [self._answer(message, payload)]

    # --- one route_request ---

    def _answer(self, request: Message, payload: RouteRequestPayload) -> Message:
        origin, destination = payload.origin, payload.destination  # replaced by the resolved places as they resolve
        try:
            origin = self._resolve(payload.origin)
            destination = origin if payload.destination == payload.origin else self._resolve(payload.destination)
            if not payload.modes:
                return self._reply(request, RouteReplyPayload(origin=origin, destination=destination), _located_summary(origin, destination))
            minutes = self._minutes(origin, destination, payload)
        except MapsError as exc:
            logger.warning("route_request %s failed: %s", request.message_id, exc.code)
            payload_out = RouteReplyPayload(error=exc.code, origin=origin, destination=destination)
            return self._reply(request, payload_out, f"Route failed: {exc.code}")

        error = "no_route" if all(m is None for m in minutes.values()) else None
        reply = RouteReplyPayload(minutes=minutes, error=error, origin=origin, destination=destination)
        return self._reply(request, reply, _route_summary(minutes))

    def _resolve(self, place: Place) -> Place:
        """The place with coordinates: as given if it has them, otherwise geocoded (and the address echoed as requested)."""
        if place.lat is not None and place.lng is not None:
            return place
        address = (place.address or "").strip()
        if not address:
            raise MapsError("geocode_failed", "empty address")
        found = self._cache.get_geocode(address)
        if found is None:
            found = self._maps.geocode(address)
            self._cache.put_geocode(address, found)
        return Place(address=place.address, lat=found.lat, lng=found.lng)

    def _minutes(self, origin: Place, destination: Place, payload: RouteRequestPayload) -> dict[Mode, float | None]:
        assert origin.lat is not None and origin.lng is not None and destination.lat is not None and destination.lng is not None
        start = LatLng(lat=origin.lat, lng=origin.lng)
        end = LatLng(lat=destination.lat, lng=destination.lng)
        modes = tuple(dict.fromkeys(payload.modes))

        known: dict[Mode, float | None] = {}
        missing: list[Mode] = []
        for mode in modes:
            hit = self._cache.get_route(start, end, mode, payload.depart_at)
            if hit is None:
                missing.append(mode)
            else:
                known[mode] = hit.minutes

        if missing:
            fresh = self._maps.route_minutes(start, end, payload.depart_at, missing)
            for mode in missing:
                minutes = _checked(fresh, mode)
                self._cache.put_route(start, end, mode, payload.depart_at, minutes)  # only good answers are cached
                known[mode] = minutes
        return {mode: known[mode] for mode in modes}

    # --- building replies ---

    @staticmethod
    def _reply(request: Message, payload: RouteReplyPayload, summary: str) -> Message:
        return build_message(
            type=MessageType.ROUTE_REPLY,
            sender=Seat.ROUTING,
            to=[request.sender],
            trip_id=request.trip_id,
            user_id=request.user_id,
            state_version=request.state_version,
            summary=summary,
            payload=payload,
            reply_to=request.message_id,
        )

    @staticmethod
    def _unexpected_type(message: Message) -> Message:
        logger.warning("Routing got a %s from %s; it only answers route_request", message.type.value, message.sender.value)
        return build_message(
            type=MessageType.ERROR,
            sender=Seat.ROUTING,
            to=[message.sender],
            trip_id=message.trip_id,
            user_id=message.user_id,
            state_version=message.state_version,
            summary=f"Routing only answers route_request, not {message.type.value}",
            payload=ErrorPayload(code=UNEXPECTED_TYPE, bad_message_id=message.message_id),
            reply_to=message.message_id,
        )


def _checked(answers: dict[Mode, float | None], mode: Mode) -> float | None:
    """One mode's answer from the map client, rounded to one decimal. A missing or nonsensical answer is an upstream error."""
    if mode not in answers:
        raise MapsError("upstream_error", f"the maps client did not answer {mode.value}")
    value = answers[mode]
    if value is None:
        return None
    if not math.isfinite(value) or value < 0:
        raise MapsError("upstream_error", f"the maps client gave {mode.value} an impossible time")
    return round(value, 1)


def _number(minutes: float) -> str:
    return str(int(minutes)) if minutes == int(minutes) else f"{minutes:.1f}"


def _route_summary(minutes: dict[Mode, float | None]) -> str:
    """"Route: 21 min walking, 12 transit, 9 driving": the unit goes on the first number only."""
    if all(m is None for m in minutes.values()):
        return f"Route: no route by {', '.join(mode.value for mode in minutes)}"
    parts: list[str] = []
    unit_written = False
    for mode, value in minutes.items():
        if value is None:
            parts.append(f"no {mode.value} route")
        else:
            parts.append(f"{_number(value)}{'' if unit_written else ' min'} {mode.value}")
            unit_written = True
    return f"Route: {', '.join(parts)}"


def _located_summary(origin: Place, destination: Place) -> str:
    if origin == destination and origin.lat is not None:
        name = (origin.address or "the place")[:_SUMMARY_ADDRESS_LIMIT]
        return f"Located {name} at {origin.lat:.4f}, {origin.lng:.4f}"
    return "Located the origin and the destination"
