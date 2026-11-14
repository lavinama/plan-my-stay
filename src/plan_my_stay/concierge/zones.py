"""Zones: the candidate areas the Stay agent searches, one map box each.

v1 uses static zones (SEAT_PROTOCOL decision 3): the demo's zones for New York, and for any other city
one zone covering the geocoded anchors plus a margin. The plan's grid scoring by Routing is not built.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from plan_my_stay import demo
from plan_my_stay.models import Anchor, Bounds, LatLng, Zone

DEFAULT_MARGIN_DEGREES = 0.02  # about 2 km of latitude: room for stays around the meetings


@runtime_checkable
class ZoneProvider(Protocol):
    def zones_for(self, city: str, anchors: Sequence[Anchor]) -> tuple[Zone, ...]:
        """The zones to search for a stay in `city`, given the geocoded meetings."""
        ...


def _is_new_york(city: str) -> bool:
    return city.strip().lower().startswith("new york")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class StaticZones:
    """The demo zones for New York; for any other city one box around the anchors, padded by `margin_degrees`.
    With no anchors there is nothing to box, so no zone is made up."""

    def __init__(self, margin_degrees: float = DEFAULT_MARGIN_DEGREES) -> None:
        if margin_degrees < 0:
            raise ValueError("margin_degrees must not be negative")
        self._margin = margin_degrees

    def zones_for(self, city: str, anchors: Sequence[Anchor]) -> tuple[Zone, ...]:
        if _is_new_york(city):
            return demo.demo_zones()
        if not anchors:
            return ()
        lats = [a.lat for a in anchors]
        lngs = [a.lng for a in anchors]
        bounds = Bounds(
            ne=LatLng(lat=_clamp(max(lats) + self._margin, -90, 90), lng=_clamp(max(lngs) + self._margin, -180, 180)),
            sw=LatLng(lat=_clamp(min(lats) - self._margin, -90, 90), lng=_clamp(min(lngs) - self._margin, -180, 180)),
        )
        name = city.split(",")[0].strip() or city.strip()
        return (Zone(id="z1", name=name, bounds=bounds),)


def widen_zones(zones: Sequence[Zone], margin_degrees: float = DEFAULT_MARGIN_DEGREES) -> tuple[Zone, ...]:
    """Each box grown by `margin_degrees` on every side (the last resort when nothing fits the budget)."""
    widened = []
    for zone in zones:
        bounds = Bounds(
            ne=LatLng(
                lat=_clamp(zone.bounds.ne.lat + margin_degrees, -90, 90),
                lng=_clamp(zone.bounds.ne.lng + margin_degrees, -180, 180),
            ),
            sw=LatLng(
                lat=_clamp(zone.bounds.sw.lat - margin_degrees, -90, 90),
                lng=_clamp(zone.bounds.sw.lng - margin_degrees, -180, 180),
            ),
        )
        widened.append(zone.model_copy(update={"bounds": bounds}))
    return tuple(widened)
