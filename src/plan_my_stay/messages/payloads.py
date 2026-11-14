"""One payload model per message type (docs/PLAN.md, "Message types").

Only `payload` changes with `type`. Models ignore unknown fields so a newer sender
doesn't break an older receiver; the envelope rejects unknown types and major versions.
"""

import datetime as dt
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from plan_my_stay.enums import MessageType, Mode, Seat, VerdictStatus
from plan_my_stay.models import (
    Address,
    Anchor,
    Budget,
    ClockTime,
    CommuteMinutes,
    Conflict,
    Dates,
    Frozen,
    Identifier,
    Latitude,
    Longitude,
    Minutes,
    Place,
    Profile,
    ProposalItem,
    Slot,
    StayOption,
    Usage,
    Zone,
)
from plan_my_stay.state import TripState
from pydantic import ConfigDict


class Constraints(Frozen):
    model_config = ConfigDict(frozen=True, extra="allow")

    max_walk_minutes: int | None = Field(default=None, ge=0)


class RequestPayload(Frozen):
    """Concierge to Stay, Restaurant, Gym."""

    city: str
    dates: Dates
    guests: int = Field(default=1, ge=1)
    anchors: tuple[Anchor, ...]
    zones: tuple[Zone, ...] = ()
    slots: tuple[Slot, ...] = ()
    profile: Profile = Profile()
    constraints: Constraints = Constraints()
    budget: Budget | None = None  # Stay only: it returns no stay priced above it
    roster: tuple[Seat, ...] = ()
    round: int = Field(default=0, ge=0, le=2)
    response_type: Literal["proposal", "stay_shortlist"] = "proposal"


class ProposalPayload(Frozen):
    """Each specialist to Concierge and Verifier.

    The Concierge also sends the merged plan to the Verifier as a proposal with
    `agent="concierge"` and the exact `state` to check. The Verifier has no other way
    to get the anchors, budget and legs, and the plan says it works from this exact state.
    """

    agent: Seat
    items: tuple[ProposalItem, ...] = ()
    usage: Usage = Usage()
    state: TripState | None = None


class ZoneFitCounts(Frozen):
    restaurants: int | None = Field(default=None, ge=0)
    gyms: int | None = Field(default=None, ge=0)


class ZoneFitItem(Frozen):
    item_id: Identifier
    name: str
    walk_minutes: Minutes | None = None
    # Restaurant: open at the meal windows. Gym: can host the preferred workout slot. None = could not tell.
    fits: bool | None = None


class ZoneFitPayload(Frozen):
    """Restaurant and Gym to Stay."""

    zone_id: Identifier
    counts: ZoneFitCounts = ZoneFitCounts()
    top_items: tuple[ZoneFitItem, ...] = ()


class StayShortlistPayload(Frozen):
    """Stay to Concierge: ranked stays priced within the budget. Empty means nothing fits (the Concierge widens)."""

    stays: tuple[StayOption, ...] = ()

    @model_validator(mode="after")
    def _ranks_run_from_one(self) -> "StayShortlistPayload":
        if [s.rank for s in self.stays] != list(range(1, len(self.stays) + 1)):
            raise ValueError("stays must be ordered with ranks 1..n")
        return self


class StayPickPayload(Frozen):
    """Stay to Restaurant, Gym and Concierge."""

    stay_id: Identifier
    zone_id: Identifier
    lat: Latitude | None = None
    lng: Longitude | None = None
    commute_minutes: CommuteMinutes = {}


class WorkoutPlace(Frozen):
    name: str
    address: Address | None = None
    lat: Latitude | None = None
    lng: Longitude | None = None


class WorkoutSlotPayload(Frozen):
    """Gym to Restaurant and Concierge."""

    date: dt.date
    start: ClockTime
    end: ClockTime
    place: WorkoutPlace

    @model_validator(mode="after")
    def _ends_after_start(self) -> "WorkoutSlotPayload":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class PreferenceLearnedPayload(Frozen):
    """Any agent to the Concierge and the specialists it affects. Only the Concierge writes it to Cognee."""

    key: Identifier
    value: JsonValue
    evidence: str


class VerdictPayload(Frozen):
    """Verifier to Concierge. `clear` and `warn` carry the hash of the exact plan that was checked."""

    status: VerdictStatus
    plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    conflicts: tuple[Conflict, ...] = ()

    @model_validator(mode="after")
    def _hash_and_conflicts_match_status(self) -> "VerdictPayload":
        if self.status is VerdictStatus.BLOCK:
            if not self.conflicts:
                raise ValueError("a block needs at least one conflict")
        elif self.plan_hash is None:
            raise ValueError("clear and warn need the plan_hash of the plan that was checked")
        return self


class ErrorPayload(Frozen):
    """Any seat to the sender of a message it could not accept."""

    code: str
    bad_message_id: str | None = None
    fields: tuple[str, ...] = ()


class RouteRequestPayload(Frozen):
    """Any seat to Routing. An empty `modes` list is a geocode-only lookup: Routing resolves the addresses and returns no minutes."""

    origin: Place
    destination: Place
    depart_at: AwareDatetime
    modes: tuple[Mode, ...] = (Mode.WALKING, Mode.TRANSIT, Mode.DRIVING)


class RouteReplyPayload(Frozen):
    """Routing to the requester: travel minutes per mode, or an error."""

    minutes: dict[Mode, Minutes | None] = {}
    error: str | None = None
    origin: Place | None = None  # as resolved: geocoded coordinates
    destination: Place | None = None

    @model_validator(mode="after")
    def _answer_or_error(self) -> "RouteReplyPayload":
        resolved = self.origin is not None or self.destination is not None
        if self.error is None and not self.minutes and not resolved:
            raise ValueError("give minutes, resolved places (a geocode-only answer), or an error")
        return self


# The conflict payload is the shared Conflict model: the same shape sits in a verdict and in trip state.
PAYLOAD_MODELS: dict[MessageType, type[Frozen]] = {
    MessageType.REQUEST: RequestPayload,
    MessageType.PROPOSAL: ProposalPayload,
    MessageType.ZONE_FIT: ZoneFitPayload,
    MessageType.STAY_SHORTLIST: StayShortlistPayload,
    MessageType.STAY_PICK: StayPickPayload,
    MessageType.WORKOUT_SLOT: WorkoutSlotPayload,
    MessageType.PREFERENCE_LEARNED: PreferenceLearnedPayload,
    MessageType.CONFLICT: Conflict,
    MessageType.VERDICT: VerdictPayload,
    MessageType.ERROR: ErrorPayload,
    MessageType.ROUTE_REQUEST: RouteRequestPayload,
    MessageType.ROUTE_REPLY: RouteReplyPayload,
}
