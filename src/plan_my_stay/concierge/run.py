"""Per-trip run state, kept in the seat by `trip_id`.

A `Run` is a frozen dataclass: every step builds a new one with `Run.with_` and the seat stores that in place of
the old one. Nothing here is mutated. Meeting addresses live in `intake` and nowhere else.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from plan_my_stay.concierge.intake import TripIntake
from plan_my_stay.enums import Seat
from plan_my_stay.fit import GymFitEntry, RestaurantFitEntry
from plan_my_stay.messages import Message
from plan_my_stay.messages.payloads import (
    ProposalPayload,
    StayPickPayload,
    StayShortlistPayload,
    VerdictPayload,
    WorkoutSlotPayload,
)
from plan_my_stay.models import LatLng, Leg, Profile
from plan_my_stay.seat import Outgoing, TextReply
from plan_my_stay.state import TripState

Result = tuple["Run", list[Outgoing]]  # what a step returns: the new Run and the messages to send

MAX_ROUNDS = 2  # a conflict sends an item back to its owner at most twice; then the plan goes out with a warning


class Phase(StrEnum):
    GEOCODING = "geocoding"  # waiting for Routing to resolve the meeting addresses
    REQUESTED = "requested"  # requests are out; nothing has come back
    COLLECTING = "collecting"  # answers are coming in
    RANKING = "ranking"  # waiting for Gym and Restaurant to measure from the candidate hotels
    ROUTING = "routing"  # waiting for Routing's travel times for the legs
    REVIEWING = "reviewing"  # the merged plan is with the Verifier
    DELIVERED = "delivered"  # the run is over: the person has the itinerary (or the reason there is none)


@dataclass(frozen=True)
class PendingLeg:
    """A leg whose Routing answer is outstanding."""

    from_id: str
    to_id: str
    depart_at: dt.datetime


@dataclass(frozen=True)
class Run:
    trip_id: str
    user_id: str
    person: str  # the BAND participant to answer
    intake: TripIntake  # the meetings and their addresses
    profile: Profile
    phase: Phase = Phase.GEOCODING
    pending_geocodes: Mapping[str, str] = field(default_factory=dict)  # route_request message_id -> meeting id
    geocoded: Mapping[str, LatLng] = field(default_factory=dict)  # meeting id -> coordinates
    state: TripState | None = None  # built once every meeting is located
    shortlist: StayShortlistPayload | None = None  # None while a (cheaper) answer is awaited
    shortlist_id: str | None = None  # the message that carried it, for `reply_to`
    proposals: Mapping[Seat, ProposalPayload] = field(default_factory=dict)  # the latest, by agent
    gym_fits: Mapping[str, GymFitEntry] = field(default_factory=dict)  # stay id -> the Gym agent's answer
    restaurant_fits: Mapping[str, RestaurantFitEntry] = field(default_factory=dict)
    queried: Mapping[Seat, frozenset[str]] = field(default_factory=dict)  # stay ids each seat was asked about
    workout_slot: WorkoutSlotPayload | None = None
    stay_pick: StayPickPayload | None = None
    pending_legs: Mapping[str, PendingLeg] = field(default_factory=dict)  # route_request message_id -> leg
    leg_order: tuple[str, ...] = ()  # the same message ids, in the order the legs go into the plan
    leg_results: Mapping[str, Leg | None] = field(default_factory=dict)  # None: Routing found no route
    verdicts: tuple[VerdictPayload, ...] = ()
    last_verdict_id: str | None = None
    verifier_request_id: str | None = None  # the merged-plan proposal the Verifier is checking now
    stay_round: int = 0  # how many times Stay was asked for cheaper options
    review_round: int = 0  # how many times a block sent items back to their owners
    widened: bool = False  # the budget was widened because nothing fit
    awaiting: frozenset[Seat] = frozenset()  # owners whose revised proposal a block is waiting for
    notes: tuple[str, ...] = ()  # explanations for the itinerary (a stay trade-off); rebuilt on every merge
    warnings: tuple[str, ...] = ()  # heads-ups that last the whole run (a widened budget)
    merge_warnings: tuple[str, ...] = ()  # heads-ups about the current merge (a travel time Routing could not find)
    requests: Mapping[Seat, Message] = field(default_factory=dict)  # the latest request sent to each specialist
    attempts: Mapping[Seat, int] = field(default_factory=dict)  # resends after an `error` reply
    dropped: frozenset[Seat] = frozenset()  # seats given up on after repeated errors

    def with_(self, **changes: Any) -> "Run":
        """A new Run with `changes` applied. The original is untouched."""
        return replace(self, **changes)

    @property
    def trip(self) -> TripState:
        """The trip state. It exists once every meeting is located, which is before any other message is handled."""
        if self.state is None:
            raise RuntimeError("the trip state is built when every meeting has been located")
        return self.state

    @property
    def roster(self) -> tuple[Seat, ...]:
        """The seats still in the run."""
        return tuple(seat for seat in (self.state.roster if self.state else ()) if seat not in self.dropped)

    @property
    def awaited_proposers(self) -> frozenset[Seat]:
        """The specialists whose `proposal` the plan needs (Stay answers with a shortlist instead)."""
        return frozenset(seat for seat in self.roster if seat is not Seat.STAY)

    @property
    def ready_to_gate(self) -> bool:
        return self.shortlist is not None and self.awaited_proposers <= set(self.proposals)


def put(mapping: Mapping[Any, Any], key: Any, value: Any) -> dict[Any, Any]:
    """A copy of `mapping` with `key` set (runs are never mutated)."""
    return {**mapping, key: value}


def drop(mapping: Mapping[Any, Any], key: Any) -> dict[Any, Any]:
    """A copy of `mapping` without `key`."""
    return {k: v for k, v in mapping.items() if k != key}


def reply(run: Run, text: str) -> TextReply:
    """Prose for the person who started this run."""
    return TextReply(text=text, mentions=(run.person,))
