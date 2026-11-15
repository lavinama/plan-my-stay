"""Where each candidate hotel is, for the Gym and Restaurant agents to measure from.

A listing's own coordinates when it has them, else the Stay agent's pick for it, else the centre of its zone (booking
cards often carry no address, only a neighbourhood). A hotel with no known location cannot be measured from, so it is
left out of the query and ranked on commute alone.
"""

from collections.abc import Sequence

from plan_my_stay.concierge.legs import locate_stay
from plan_my_stay.enums import LocationPrecision
from plan_my_stay.fit import HotelPoint
from plan_my_stay.messages.payloads import StayPickPayload
from plan_my_stay.models import StayOption, Zone


def _has_own_location(stay: StayOption, pick: StayPickPayload | None) -> bool:
    if stay.lat is not None and stay.lng is not None:
        return True
    return pick is not None and pick.stay_id == stay.stay_id and pick.lat is not None and pick.lng is not None


def hotel_points(stays: Sequence[StayOption], zones: Sequence[Zone], pick: StayPickPayload | None) -> tuple[HotelPoint, ...]:
    points: list[HotelPoint] = []
    for stay in stays:
        location = locate_stay(stay, pick, zones)
        if location is None:
            continue
        precision = LocationPrecision.EXACT if _has_own_location(stay, pick) else LocationPrecision.ZONE
        points.append(
            HotelPoint(
                stay_id=stay.stay_id,
                zone_id=stay.zone_id,
                name=stay.name,
                lat=location.lat,
                lng=location.lng,
                location_precision=precision,
            )
        )
    return tuple(points)
