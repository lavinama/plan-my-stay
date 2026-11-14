"""Small pure helpers shared by the cache, the fixture client and the seat."""

import math
import re
from datetime import UTC, datetime

from plan_my_stay.models import LatLng

_EARTH_RADIUS_KM = 6371.0088
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_address(address: str) -> str:
    """Lower case, punctuation gone, spaces collapsed: "199 Water St., New York" and "199 water st new york" match."""
    return _NOT_ALNUM.sub(" ", address.lower()).strip()


def street_part(address: str) -> str:
    """The normalized text before the first comma ("368 9th ave"), for matching an address given without its city."""
    return normalize_address(address.split(",", 1)[0])


def coord_key(point: LatLng) -> str:
    """Five decimals is about a metre: enough to tell two buildings apart, coarse enough to absorb float noise."""
    return f"{point.lat:.5f},{point.lng:.5f}"


def hour_key(when: datetime) -> str:
    """The hour a departure falls in (truncated, in UTC so the same instant gives the same key in any offset)."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("a departure time must carry an offset")
    return when.astimezone(UTC).strftime("%Y-%m-%dT%HZ")


def haversine_km(a: LatLng, b: LatLng) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    d_lat, d_lng = lat2 - lat1, math.radians(b.lng - a.lng)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))
