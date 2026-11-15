"""Immutable value objects shared by the messages and the trip state.

Dependency order (no cycles): enums, models -> state -> messages.

Rules from docs/PLAN.md ("Rules that keep every message parseable"): unknown is
`null`, never "n/a"; clock times are 24-hour HH:MM; hours maps use Mon..Sun keys;
money is a number plus a currency code; coordinates are decimal degrees.
"""

import datetime as dt
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from plan_my_stay.enums import LocationPrecision, Mode, Seat


class Frozen(BaseModel):
    """Base for every model: immutable, and unknown fields are ignored so a newer sender doesn't break an older receiver."""

    model_config = ConfigDict(frozen=True, extra="ignore")


_CLOCK = r"([01]\d|2[0-3]):[0-5]\d"

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Minutes = Annotated[float, Field(ge=0)]
ClockTime = Annotated[str, StringConstraints(pattern=f"^{_CLOCK}$")]
Period = Annotated[str, StringConstraints(pattern=f"^{_CLOCK}-{_CLOCK}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
SlotKind = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]*$")]
Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# "17:00-22:00", or a list of periods for split hours. A period whose end is not after its start runs past midnight.
Hours = dict[Day, Period | tuple[Period, ...]]
# Minutes per mode, keyed by anchor id: {"B": {"walking": 12, "transit": 9, "driving": 8}}.
CommuteMinutes = dict[str, dict[Mode, Minutes | None]]


class CostBasis:
    """What a `cost.amount` means. A plain str on the wire so a new basis never breaks an older receiver."""

    UNKNOWN = "unknown"
    CARD_TOTAL_FEES_UNKNOWN = "card_total_fees_unknown"  # stay total whose fee basis the site doesn't show
    TOTAL_INCL_FEES = "total_incl_fees"
    DAY_PASS = "day_pass"

    FEES_UNKNOWN = frozenset({UNKNOWN, CARD_TOTAL_FEES_UNKNOWN})  # the budget check adds a reserve to these


class LatLng(Frozen):
    lat: Latitude
    lng: Longitude


class Bounds(Frozen):
    ne: LatLng
    sw: LatLng


class Zone(Frozen):
    id: Identifier
    name: str
    bounds: Bounds


class Anchor(Frozen):
    """A fixed point: a meeting's place and time window. Agents never see meeting names or attendees."""

    id: Identifier
    lat: Latitude
    lng: Longitude
    start: AwareDatetime
    end: AwareDatetime
    counts_for_stay: bool  # false for a meeting before check-in: it doesn't constrain where the stay is

    @model_validator(mode="after")
    def _ends_after_start(self) -> "Anchor":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Window(Frozen):
    start: ClockTime
    end: ClockTime

    @model_validator(mode="after")
    def _ends_after_start(self) -> "Window":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Slot(Frozen):
    """A time window an agent should fill: a meal or a workout."""

    id: Identifier
    kind: SlotKind  # lunch, dinner, workout, ...
    date: dt.date
    window: Window


class Dates(Frozen):
    check_in: dt.date
    check_out: dt.date

    @model_validator(mode="after")
    def _check_out_after_check_in(self) -> "Dates":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class Profile(Frozen):
    """What the shared profile remembers. Extra keys are kept, because agents learn new preferences at run time."""

    model_config = ConfigDict(frozen=True, extra="allow")

    diet: tuple[str, ...] = ()
    cuisines: tuple[str, ...] = ()
    gym_types: tuple[str, ...] = ()
    workout_times: tuple[ClockTime, ...] = ()
    stay_preferences: tuple[str, ...] = ()
    price_comfort: str | None = None
    transport_mode: Mode | None = None


class Budget(Frozen):
    currency: Currency = "USD"
    amount: float = Field(gt=0)


class Place(Frozen):
    """Where something is: an address for Routing to geocode, coordinates, or both."""

    address: str | None = None
    lat: Latitude | None = None
    lng: Longitude | None = None

    @model_validator(mode="after")
    def _located(self) -> "Place":
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng go together")
        if self.address is None and self.lat is None:
            raise ValueError("give an address or coordinates")
        return self


class Address(Frozen):
    street: str | None = None
    city: str | None = None
    zip: str | None = None


class When(Frozen):
    date: dt.date
    start: ClockTime
    end: ClockTime

    @model_validator(mode="after")
    def _ends_after_start(self) -> "When":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Cost(Frozen):
    currency: Currency = "USD"
    amount: float | None = Field(default=None, ge=0)
    basis: str = CostBasis.UNKNOWN


class Fit(Frozen):
    reason: str
    profile_fields: tuple[str, ...] = ()


class Source(Frozen):
    url: str
    fetched_at: AwareDatetime


class Reader(Frozen):
    model: str
    escalated: bool = False


class Usage(Frozen):
    """What a proposal cost to produce. Extra keys (tokens, keenable_requests, maps_elements) feed the cost ledger."""

    model_config = ConfigDict(frozen=True, extra="allow")

    calls: int = Field(default=0, ge=0)
    credits: float = Field(default=0, ge=0)


class ProposalItem(Frozen):
    item_id: Identifier
    slot_id: str | None = None  # a stay fills no slot
    name: str
    address: Address | None = None
    location_precision: LocationPrecision = LocationPrecision.EXACT
    lat: Latitude | None = None
    lng: Longitude | None = None
    when: When | None = None
    cost: Cost = Cost()
    fit: Fit | None = None
    hours: Hours | None = None
    source: Source
    reader: Reader | None = None
    dedupe_key: str
    commute_minutes: CommuteMinutes | None = None  # stays only
    free_cancel_deadline: AwareDatetime | None = None  # stays only, when the site shows one

    @model_validator(mode="after")
    def _located(self) -> "ProposalItem":
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng go together")
        if self.location_precision is LocationPrecision.EXACT and self.lat is None:
            raise ValueError("an exact location needs lat and lng")
        return self


class StayOption(Frozen):
    """One stay in the Stay agent's ranked shortlist (and, when chosen, the trip's stay)."""

    rank: int = Field(ge=1)
    deciding_factor: str
    stay_id: Identifier
    zone_id: Identifier
    name: str | None = None
    total_price: float = Field(ge=0)
    currency: Currency = "USD"
    cost_basis: str = CostBasis.UNKNOWN
    lat: Latitude | None = None
    lng: Longitude | None = None
    commute_minutes: CommuteMinutes = {}
    # Mean over the meetings that count for the stay of the fastest-mode minutes: what the Stay agent ranks by.
    average_commute_minutes: Minutes | None = None


class Leg(Frozen):
    """One trip between two points, with Routing's times and the mode the Concierge proposed (the fastest)."""

    from_id: Identifier  # an anchor id, "stay", or an item id
    to_id: Identifier
    depart_at: AwareDatetime
    minutes_by_mode: dict[Mode, Minutes | None]
    chosen_mode: Mode | None = None


class PlanEntry(Frozen):
    """A chosen specialist item and the seat that owns it, so a conflict goes to the right agent."""

    agent: Seat
    item: ProposalItem


class Conflict(Frozen):
    """A rule the plan broke, on which item, and how the owner could fix it."""

    rule: str
    item_id: str
    detail: str
    suggestion: str | None = None
