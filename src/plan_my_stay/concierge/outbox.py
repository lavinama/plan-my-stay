"""Every machine message the Concierge sends, built with `build_message` (never by hand).

Privacy: meeting addresses go out only in the geocode-only lookup to Routing, the one seat that must read an
address to resolve it. Specialists and the Verifier get anchors (coordinates and time windows); leg requests
carry coordinates only.
"""

from collections.abc import Sequence

from plan_my_stay.concierge.assemble import profile_for, slots_for
from plan_my_stay.concierge.fmt import fmt_day, fmt_money
from plan_my_stay.concierge.intake import MeetingIntake
from plan_my_stay.concierge.legs import LegSpec
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import Message, build_message
from plan_my_stay.fit import DEFAULT_RADIUS_MINUTES, HotelPoint
from plan_my_stay.messages.payloads import (
    Constraints,
    HotelQueryPayload,
    ProposalPayload,
    RequestPayload,
    RouteRequestPayload,
)
from plan_my_stay.models import Budget, Dates, Place
from plan_my_stay.state import TripState

MAX_WALK_MINUTES = 15  # the plan's example constraint: restaurants and gyms within a short walk

_WHAT = {
    Seat.STAY: "stays",
    Seat.RESTAURANT: "meals",
    Seat.GYM: "a gym and a workout slot",
}


def geocode_request(*, trip_id: str, user_id: str, meeting: MeetingIntake) -> Message:
    """Ask Routing to resolve one meeting's address (`modes=[]`: origin and destination are both the address)."""
    place = Place(address=meeting.address)
    return build_message(
        type=MessageType.ROUTE_REQUEST,
        sender=Seat.CONCIERGE,
        to=[Seat.ROUTING],
        trip_id=trip_id,
        user_id=user_id,
        state_version=0,
        summary=f"Locate meeting {meeting.id}",
        payload=RouteRequestPayload(origin=place, destination=place, depart_at=meeting.start, modes=()),
    )


def specialist_request(
    state: TripState,
    seat: Seat,
    *,
    round: int = 0,  # noqa: A002 - the wire field is called `round`
    budget: Budget | None = None,
    reply_to: str | None = None,
    note: str = "",
) -> Message:
    """A `request` to Stay, Restaurant or Gym. Only Stay gets the budget (and answers with a shortlist)."""
    is_stay = seat is Seat.STAY
    payload = RequestPayload(
        city=state.city,
        dates=Dates(check_in=state.check_in, check_out=state.check_out),
        guests=state.guests,
        anchors=state.anchors,
        zones=state.zones,
        slots=slots_for(seat, state.slots),
        profile=profile_for(seat, state.profile),
        constraints=Constraints(max_walk_minutes=MAX_WALK_MINUTES),
        budget=(budget or state.budget) if is_stay else None,
        roster=state.roster,
        round=round,
        response_type="stay_shortlist" if is_stay else "proposal",
    )
    dates = f"{fmt_day(state.check_in)} to {fmt_day(state.check_out)}"
    cap = f" within {fmt_money(payload.budget.amount, payload.budget.currency)}" if payload.budget else ""
    return build_message(
        type=MessageType.REQUEST,
        sender=Seat.CONCIERGE,
        to=[seat],
        trip_id=state.trip_id,
        user_id=state.user_id,
        state_version=state.state_version,
        summary=f"Find {_WHAT[seat]} in {state.city}, {dates}{cap}{note}",
        payload=payload,
        reply_to=reply_to,
    )


def hotel_query(state: TripState, seat: Seat, hotels: Sequence[HotelPoint]) -> Message:
    """Ask Gym or Restaurant to measure from each candidate hotel (coordinates only: no meeting address)."""
    what = "nearest gym" if seat is Seat.GYM else f"restaurants within a {DEFAULT_RADIUS_MINUTES:.0f}-minute walk"
    return build_message(
        type=MessageType.HOTEL_QUERY,
        sender=Seat.CONCIERGE,
        to=[seat],
        trip_id=state.trip_id,
        user_id=state.user_id,
        state_version=state.state_version,
        summary=f"Measure from {len(hotels)} hotel{'s' if len(hotels) != 1 else ''}: {what}",
        payload=HotelQueryPayload(hotels=tuple(hotels), radius_minutes=DEFAULT_RADIUS_MINUTES),
    )


def leg_request(state: TripState, spec: LegSpec) -> Message:
    """Ask Routing for the travel times of one leg: coordinates only, never an address."""
    return build_message(
        type=MessageType.ROUTE_REQUEST,
        sender=Seat.CONCIERGE,
        to=[Seat.ROUTING],
        trip_id=state.trip_id,
        user_id=state.user_id,
        state_version=state.state_version,
        summary=f"Travel times {spec.from_id} to {spec.to_id}",
        payload=RouteRequestPayload(origin=spec.origin, destination=spec.destination, depart_at=spec.depart_at),
    )


def verifier_proposal(state: TripState, *, reply_to: str | None = None) -> Message:
    """The merged plan for the Verifier: a `proposal` with agent="concierge" and the exact state to check."""
    return build_message(
        type=MessageType.PROPOSAL,
        sender=Seat.CONCIERGE,
        to=[Seat.VERIFIER],
        trip_id=state.trip_id,
        user_id=state.user_id,
        state_version=state.state_version,
        summary=f"Merged plan v{state.state_version} for review (round {state.round})",
        payload=ProposalPayload(agent=Seat.CONCIERGE, state=state),
        reply_to=reply_to,
    )


def resend(message: Message) -> Message:
    """The same request again under a new message id (after the receiver answered it with an `error`)."""
    return build_message(
        type=message.type,
        sender=message.sender,
        to=message.to,
        trip_id=message.trip_id,
        user_id=message.user_id,
        state_version=message.state_version,
        summary=message.summary[:280] + " (resent)",
        payload=message.payload,
    )
