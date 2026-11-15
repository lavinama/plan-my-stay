"""A deterministic offline MapsClient for demos, the rehearsal and tests that want realistic numbers.

The three demo meeting addresses geocode to the demo anchors; any other address gets a stable
pseudo-location on the island of Manhattan (derived from a hash, so it is the same in every
process). Travel minutes come from straight-line distance and ignore the departure time:

    walking  4.8 km/h
    transit  15 km/h plus a 5 minute wait
    driving  18 km/h plus 3 minutes (parking, getting to the car)

The same place twice is 0 minutes in every mode.
"""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime

from plan_my_stay import demo
from plan_my_stay.enums import Mode
from plan_my_stay.models import Anchor, LatLng
from plan_my_stay.routing.geo import haversine_km, normalize_address, street_part
from plan_my_stay.routing.maps import GeocodeResult, MapsError

WALKING_KMH = 4.8
TRANSIT_KMH, TRANSIT_WAIT_MIN = 15.0, 5.0
DRIVING_KMH, DRIVING_FIXED_MIN = 18.0, 3.0

# Manhattan is a strip about 2 km wide that leans east as it goes north. Pseudo-locations sit on this
# centre line (Wall Street to Harlem), up to HALF_WIDTH_DEG of longitude to either side. It stops at
# 40.82 N: further north the island narrows and bends west of this line, and points would fall in the Harlem River.
_SOUTH = LatLng(lat=40.7100, lng=-74.0080)
_NORTH = LatLng(lat=40.8200, lng=-73.9491)
_HALF_WIDTH_DEG = 0.008


def _unit_floats(text: str) -> tuple[float, float]:
    """Two stable floats in [0, 1) from the text (sha256, not `hash()`, which changes between processes)."""
    digest = hashlib.sha256(normalize_address(text).encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") / 2**64, int.from_bytes(digest[8:16], "big") / 2**64)


def _pseudo_location(address: str) -> LatLng:
    along, across = _unit_floats(address)
    lat = _SOUTH.lat + along * (_NORTH.lat - _SOUTH.lat)
    centre_lng = _SOUTH.lng + along * (_NORTH.lng - _SOUTH.lng)
    return LatLng(lat=round(lat, 6), lng=round(centre_lng + (across * 2 - 1) * _HALF_WIDTH_DEG, 6))


class FixtureMapsClient:
    def __init__(self, addresses: Mapping[str, str] | None = None, anchors: Sequence[Anchor] | None = None) -> None:
        """`addresses` maps anchor id to address (default: the demo meetings), `anchors` gives their coordinates."""
        known_addresses = demo.MEETING_ADDRESSES if addresses is None else addresses
        by_id = {a.id: a for a in (demo.demo_anchors() if anchors is None else anchors)}
        self._known: dict[str, GeocodeResult] = {}
        self._known_by_street: dict[str, GeocodeResult] = {}
        for anchor_id, address in known_addresses.items():
            anchor = by_id[anchor_id]
            result = GeocodeResult(lat=anchor.lat, lng=anchor.lng, formatted_address=address)
            self._known[normalize_address(address)] = result
            self._known_by_street[street_part(address)] = result

    def geocode(self, address: str) -> GeocodeResult:
        if not normalize_address(address):
            raise MapsError("geocode_failed", "empty address")
        known = self._known.get(normalize_address(address)) or self._known_by_street.get(street_part(address))
        if known is not None:
            return known
        point = _pseudo_location(address)
        return GeocodeResult(lat=point.lat, lng=point.lng, formatted_address=address.strip())

    def route_minutes(
        self, origin: LatLng, destination: LatLng, depart_at: datetime, modes: Sequence[Mode]
    ) -> dict[Mode, float | None]:
        km = haversine_km(origin, destination)
        return {mode: _minutes(km, mode) for mode in modes}


def _minutes(km: float, mode: Mode) -> float:
    if km == 0:
        return 0.0
    if mode is Mode.WALKING:
        minutes = km / WALKING_KMH * 60
    elif mode is Mode.TRANSIT:
        minutes = km / TRANSIT_KMH * 60 + TRANSIT_WAIT_MIN
    else:
        minutes = km / DRIVING_KMH * 60 + DRIVING_FIXED_MIN
    return round(minutes, 1)
