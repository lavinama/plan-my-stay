"""A scripted MapsClient for the seat tests: records every call, fails on demand."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from plan_my_stay.enums import Mode
from plan_my_stay.models import LatLng
from plan_my_stay.routing.maps import GeocodeResult, MapsError

DEFAULT_MINUTES: dict[Mode, float | None] = {Mode.WALKING: 21.0, Mode.TRANSIT: 12.0, Mode.DRIVING: 9.0}


@dataclass
class FakeMapsClient:
    locations: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    geocode_errors: Mapping[str, str] = field(default_factory=dict)  # address -> error code
    minutes: Mapping[Mode, float | None] = field(default_factory=lambda: dict(DEFAULT_MINUTES))
    route_error: str | None = None
    geocode_calls: list[str] = field(default_factory=list)
    route_calls: list[tuple[LatLng, LatLng, datetime, tuple[Mode, ...]]] = field(default_factory=list)

    def geocode(self, address: str) -> GeocodeResult:
        self.geocode_calls.append(address)
        if address in self.geocode_errors:
            raise MapsError(self.geocode_errors[address])
        lat, lng = self.locations.get(address, (40.7500, -73.9900))
        return GeocodeResult(lat=lat, lng=lng, formatted_address=f"{address} (formatted)")

    def route_minutes(
        self, origin: LatLng, destination: LatLng, depart_at: datetime, modes: Sequence[Mode]
    ) -> dict[Mode, float | None]:
        self.route_calls.append((origin, destination, depart_at, tuple(modes)))
        if self.route_error is not None:
            raise MapsError(self.route_error)
        return {mode: self.minutes.get(mode) for mode in modes}
