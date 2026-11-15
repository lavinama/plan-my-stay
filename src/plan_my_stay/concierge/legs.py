"""Which trips the Concierge asks Routing for, and where the stay is.

A day runs event to event. On the days of the stay the traveler starts at the stay (except on check-in day, when
they go straight to the first event) and ends at the stay when a later day has events too. Routing gets
coordinates only: never an address.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from plan_my_stay.concierge.timeline import Event, build_events
from plan_my_stay.messages.payloads import StayPickPayload
from plan_my_stay.models import LatLng, Place, StayOption, Zone
from plan_my_stay.state import TripState

LEAVE_EARLY = dt.timedelta(hours=1)  # the trip from the stay to the day's first event starts an hour before it
_SAME_PLACE_DEGREES = 1e-4  # about ten metres


@dataclass(frozen=True)
class LegSpec:
    """One trip to ask Routing about: ids as in `Leg` ("stay", an anchor id or an item id), a departure and coordinates."""

    from_id: str
    to_id: str
    depart_at: dt.datetime
    origin: Place
    destination: Place


@dataclass(frozen=True)
class _Point:
    id: str
    lat: float
    lng: float


@dataclass(frozen=True)
class _Stop(_Point):
    """A timed event at a place."""

    start: dt.datetime
    end: dt.datetime


def locate_stay(stay: StayOption, pick: StayPickPayload | None, zones: Sequence[Zone]) -> LatLng | None:
    """The stay's own coordinates, else the Stay agent's pick for that stay, else the centre of its zone, else None."""
    if stay.lat is not None and stay.lng is not None:
        return LatLng(lat=stay.lat, lng=stay.lng)
    if pick is not None and pick.stay_id == stay.stay_id and pick.lat is not None and pick.lng is not None:
        return LatLng(lat=pick.lat, lng=pick.lng)
    zone = next((z for z in zones if z.id == stay.zone_id), None)
    if zone is None:
        return None
    return LatLng(lat=(zone.bounds.ne.lat + zone.bounds.sw.lat) / 2, lng=(zone.bounds.ne.lng + zone.bounds.sw.lng) / 2)


def _located_stops(events: Sequence[Event]) -> list[_Stop]:
    """The events whose exact location is known (the others are left out of the legs)."""
    return [_Stop(e.id, e.lat, e.lng, e.start, e.end) for e in events if e.lat is not None and e.lng is not None]


def _same_place(a: _Point, b: _Point) -> bool:
    return abs(a.lat - b.lat) < _SAME_PLACE_DEGREES and abs(a.lng - b.lng) < _SAME_PLACE_DEGREES


def _spec(origin: _Point, destination: _Point, depart_at: dt.datetime) -> LegSpec:
    return LegSpec(
        from_id=origin.id,
        to_id=destination.id,
        depart_at=depart_at,
        origin=Place(lat=origin.lat, lng=origin.lng),
        destination=Place(lat=destination.lat, lng=destination.lng),
    )


def plan_legs(state: TripState, stay_location: LatLng | None) -> tuple[LegSpec, ...]:
    """The legs to ask Routing for. Events with no time or no coordinates are left out (see `leg_gaps`)."""
    located = _located_stops(build_events(state))
    stay = _Point("stay", stay_location.lat, stay_location.lng) if stay_location and state.stay is not None else None
    days = sorted({stop.start.date() for stop in located})
    specs: list[LegSpec] = []
    for index, day in enumerate(days):
        stops = [stop for stop in located if stop.start.date() == day]
        starts_at_stay = state.check_in < day <= state.check_out
        returns_to_stay = state.check_in <= day < state.check_out and any(
            d <= state.check_out for d in days[index + 1 :]
        )
        if stay is not None and starts_at_stay and not _same_place(stay, stops[0]):
            specs.append(_spec(stay, stops[0], stops[0].start - LEAVE_EARLY))
        for here, there in zip(stops, stops[1:], strict=False):
            if not _same_place(here, there):
                specs.append(_spec(here, there, here.end))
        if stay is not None and returns_to_stay and not _same_place(stops[-1], stay):
            specs.append(_spec(stops[-1], stay, stops[-1].end))
    return tuple(specs)


def leg_gaps(state: TripState) -> tuple[str, ...]:
    """Plain-language notes for items whose travel time cannot be checked: no exact location, or no time."""
    notes: list[str] = []
    for entry in state.entries:
        item = entry.item
        if item.when is None:
            notes.append(f"{item.name} has no time yet, so travel to it is not checked.")
        elif item.lat is None or item.lng is None:
            notes.append(f"{item.name} has no exact location yet, so travel to it is not checked.")
    return tuple(notes)
