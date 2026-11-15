"""Straight-line distance and a walking-time stand-in for the Routing seat.

The rehearsal Gym and Restaurant stubs answer "how far is the nearest gym" and "which restaurants are within a
10-minute walk" from coordinates alone, with no network. Real specialists ask the Routing seat for walking times;
until they do, `walk_minutes` is a rough, deterministic substitute that is good enough to rank hotels.

Everything here is pure: no I/O, no state.
"""

import math

EARTH_RADIUS_M = 6_371_000.0  # mean radius; the haversine error against the real ellipsoid is under 0.5%

STREET_DETOUR_FACTOR = 1.3  # streets are not straight: a walk is about 30% longer than the line between its ends
WALKING_SPEED_M_PER_MIN = 80.0  # 4.8 km/h, a brisk but ordinary pace


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two points given in decimal degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    # min() guards against a value a hair above 1.0 from rounding at antipodal points, which would crash asin.
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def walk_minutes(distance_m: float) -> float:
    """Walking minutes for a straight-line distance: the distance times a 1.3 street-detour factor, at 4.8 km/h.

    That is `distance_m * 1.3 / 80`: 800 m in a straight line is 13 minutes on foot. This is a stand-in for
    Routing, not a route: it ignores one-way streets, crossings and hills.
    """
    if distance_m < 0:
        raise ValueError(f"a distance cannot be negative: {distance_m}")
    return distance_m * STREET_DETOUR_FACTOR / WALKING_SPEED_M_PER_MIN


def within_walk(distance_m: float, minutes: float) -> bool:
    """True when a straight-line distance can be walked in `minutes` or less (the limit itself counts)."""
    return walk_minutes(distance_m) <= minutes
