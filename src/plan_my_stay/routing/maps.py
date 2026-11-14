"""What the Routing seat needs from a map provider, and nothing more.

Two implementations: `GoogleMapsClient` (live) and `FixtureMapsClient` (offline, for demos and the
rehearsal). The seat only knows this interface, so tests use a scripted fake.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from plan_my_stay.enums import Mode
from plan_my_stay.models import LatLng


class MapsErrorCode(StrEnum):
    """The short codes that travel in `route_reply.error`."""

    GEOCODE_FAILED = "geocode_failed"  # the address matched nothing
    NO_ROUTE = "no_route"  # no route in any requested mode (the seat reports this; clients return None per mode)
    QUOTA_EXCEEDED = "quota_exceeded"  # our own call cap or the provider's quota
    UPSTREAM_ERROR = "upstream_error"  # the provider failed, was unreachable or answered nonsense


class MapsError(Exception):
    """A lookup failed. `code` is one of `MapsErrorCode`; the text never holds a key, URL or provider message."""

    def __init__(self, code: MapsErrorCode | str, detail: str = "") -> None:
        self.code: str = MapsErrorCode(code).value  # an unknown code is a bug, so fail loudly here
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lng: float
    formatted_address: str


@runtime_checkable
class MapsClient(Protocol):
    def geocode(self, address: str) -> GeocodeResult:
        """Resolve an address. Raises `MapsError` (`geocode_failed`, `quota_exceeded`, `upstream_error`)."""
        ...

    def route_minutes(
        self, origin: LatLng, destination: LatLng, depart_at: datetime, modes: Sequence[Mode]
    ) -> dict[Mode, float | None]:
        """Minutes (one decimal) per requested mode; `None` for a mode with no route. `depart_at` is aware.

        Raises `MapsError` (`quota_exceeded`, `upstream_error`) when a mode could not be answered at all.
        """
        ...
