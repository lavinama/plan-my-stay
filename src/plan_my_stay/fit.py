"""What each specialist reports about each hotel, and the Concierge's ranking built from it.

The Stay agent ranks hotels by average commute to the meetings. For every candidate hotel the Gym agent reports
the nearest gym (by distance) and the Restaurant agent the average rank of the restaurants within a walk of it.
The Concierge combines the three into `RankedStay`s (docs/SEAT_PROTOCOL.md, section 5).

Dependency order: enums, models -> fit -> state -> messages.
"""

from pydantic import Field, model_validator

from plan_my_stay.enums import LocationPrecision
from plan_my_stay.models import (
    CostBasis,
    Currency,
    Frozen,
    Identifier,
    Latitude,
    Longitude,
    Minutes,
)

DEFAULT_RADIUS_MINUTES = 10.0  # "restaurants within a 10-minute radius of the hotel"


class HotelPoint(Frozen):
    """A candidate hotel to measure from. The Concierge fills `lat`/`lng`: the listing's own when it has one,
    else the centre of its zone (`location_precision` = zone), because booking cards often carry no address."""

    stay_id: Identifier
    zone_id: Identifier
    name: str | None = None
    lat: Latitude
    lng: Longitude
    location_precision: LocationPrecision = LocationPrecision.EXACT


class NearbyGym(Frozen):
    item_id: Identifier | None = None
    name: str
    distance_m: float = Field(ge=0)  # straight-line metres from the hotel
    walk_minutes: Minutes | None = None
    lat: Latitude | None = None
    lng: Longitude | None = None


class GymFitEntry(Frozen):
    stay_id: Identifier
    nearest_gym: NearbyGym | None = None  # None: no gym of the user's type was found


class NearbyRestaurant(Frozen):
    item_id: Identifier | None = None
    name: str
    rank: int = Field(ge=1)  # the Restaurant agent's own rank of this restaurant: 1 is the best fit
    walk_minutes: Minutes | None = None


class RestaurantFitEntry(Frozen):
    stay_id: Identifier
    count: int = Field(ge=0)  # restaurants within the radius of this hotel
    average_rank: float | None = Field(default=None, ge=1)  # mean of their ranks; None when there are none
    top: tuple[NearbyRestaurant, ...] = ()

    @model_validator(mode="after")
    def _average_matches_count(self) -> "RestaurantFitEntry":
        if self.count == 0 and self.average_rank is not None:
            raise ValueError("no restaurants in the radius means no average rank")
        if self.count > 0 and self.average_rank is None:
            raise ValueError("restaurants in the radius need an average rank")
        return self


class RankedStay(Frozen):
    """One hotel in the Concierge's ranking, with the numbers it was ranked on (None: that agent gave none)."""

    rank: int = Field(ge=1)
    stay_id: Identifier
    zone_id: Identifier
    name: str | None = None
    total_price: float = Field(ge=0)
    currency: Currency = "USD"
    cost_basis: str = CostBasis.UNKNOWN
    score: float = Field(ge=0, le=1)  # weighted, 1 is best on every factor
    average_commute_minutes: float | None = None
    nearest_gym_m: float | None = None
    nearest_gym_name: str | None = None
    restaurant_count: int | None = None
    restaurant_average_rank: float | None = None
    deciding_factor: str  # commute, gym or restaurants: what put it ahead of the next hotel
    scores: dict[str, float] = Field(default_factory=dict)  # per factor, 0 to 1; absent when that agent did not answer
