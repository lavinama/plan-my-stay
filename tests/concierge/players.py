"""Plays the seats the Concierge talks to, by hand: Routing, the specialists and the Verifier.

Every message is built with `build_message`, like the real seats will. `Recorder` wraps a seat and logs
everything it sends, so a test can assert on the whole conversation. `Flow` drives the demo trip step by step.
"""

from collections.abc import Iterable, Sequence

from plan_my_stay import demo
from plan_my_stay.concierge import ConciergeSeat
from plan_my_stay.enums import MessageType, Mode, Seat, VerdictStatus
from plan_my_stay.messages import Message, build_message
from plan_my_stay.messages.payloads import (
    ErrorPayload,
    PreferenceLearnedPayload,
    ProposalPayload,
    RouteReplyPayload,
    StayPickPayload,
    StayShortlistPayload,
    VerdictPayload,
    WorkoutPlace,
    WorkoutSlotPayload,
)
from plan_my_stay.models import (
    Conflict,
    CostBasis,
    Place,
    ProposalItem,
    StayOption,
    When,
)
from plan_my_stay.seat import Outgoing, TextReply
from tests.concierge.fakes import FakeMemory

PERSON = "Mario"
ADDRESS_FRAGMENTS = ("199 Water St", "368 9th Ave", "524 Broadway")

# What a real Routing seat would answer for the demo's meeting addresses.
GEOCODES = {demo.MEETING_ADDRESSES[a.id]: (a.lat, a.lng) for a in demo.demo_anchors()}
DEFAULT_MINUTES = {Mode.WALKING: 30.0, Mode.TRANSIT: 15.0, Mode.DRIVING: 12.0}


def messages(out: Iterable[Outgoing], type_: MessageType | None = None, to: Seat | None = None) -> list[Message]:
    return [
        m for m in out if isinstance(m, Message) and (type_ is None or m.type is type_) and (to is None or to in m.to)
    ]


def replies(out: Iterable[Outgoing]) -> list[TextReply]:
    return [m for m in out if isinstance(m, TextReply)]


def stay(rank: int = 1, price: float = 640.0, basis: str = CostBasis.TOTAL_INCL_FEES, **extra) -> StayOption:
    return StayOption(
        rank=rank,
        deciding_factor=extra.pop("deciding_factor", "commute"),
        stay_id=extra.pop("stay_id", f"stay-{rank}"),
        zone_id=extra.pop("zone_id", "z1"),
        name=extra.pop("name", f"Quiet stay {rank}"),
        total_price=price,
        cost_basis=basis,
        lat=extra.pop("lat", 40.7466),
        lng=extra.pop("lng", -74.0000),
        **extra,
    )


def _send(
    trip: Message,
    sender: Seat,
    type_: MessageType,
    payload,
    summary: str,
    *,
    to: Seat = Seat.CONCIERGE,
    reply_to: str | None = None,
) -> Message:
    return build_message(
        type=type_,
        sender=sender,
        to=[to],
        trip_id=trip.trip_id,
        user_id=trip.user_id,
        state_version=trip.state_version,
        summary=summary,
        payload=payload,
        reply_to=reply_to,
    )


def geocode_reply(request: Message) -> Message:
    address = request.payload.origin.address
    lat, lng = GEOCODES[address]
    place = Place(address=address, lat=lat, lng=lng)
    return _send(
        request,
        Seat.ROUTING,
        MessageType.ROUTE_REPLY,
        RouteReplyPayload(origin=place, destination=place),
        "Resolved address",
        reply_to=request.message_id,
    )


def route_reply(request: Message, minutes: dict[Mode, float | None] | None = None) -> Message:
    return _send(
        request,
        Seat.ROUTING,
        MessageType.ROUTE_REPLY,
        RouteReplyPayload(minutes=minutes or DEFAULT_MINUTES),
        "Travel times",
        reply_to=request.message_id,
    )


def failed_route_reply(request: Message, error: str = "geocode_failed") -> Message:
    return _send(
        request,
        Seat.ROUTING,
        MessageType.ROUTE_REPLY,
        RouteReplyPayload(error=error),
        "Routing failed",
        reply_to=request.message_id,
    )


def stay_shortlist(trip: Message, *stays: StayOption, reply_to: str | None = None) -> Message:
    return _send(
        trip,
        Seat.STAY,
        MessageType.STAY_SHORTLIST,
        StayShortlistPayload(stays=stays),
        f"{len(stays)} stays",
        reply_to=reply_to,
    )


def proposal(trip: Message, agent: Seat, *items: ProposalItem) -> Message:
    return _send(
        trip,
        agent,
        MessageType.PROPOSAL,
        ProposalPayload(agent=agent, items=items),
        f"{len(items)} options from {agent.value}",
    )


def workout_slot(trip: Message) -> Message:
    payload = WorkoutSlotPayload(
        date=demo.at(17, 17).date(), start="17:30", end="18:15", place=WorkoutPlace(name="Example HIIT Studio")
    )
    return _send(trip, Seat.GYM, MessageType.WORKOUT_SLOT, payload, "Workout Tue 17:30")


def stay_pick(
    trip: Message, stay_id: str = "stay-1", lat: float | None = 40.7466, lng: float | None = -74.0
) -> Message:
    payload = StayPickPayload(stay_id=stay_id, zone_id="z1", lat=lat, lng=lng)
    return _send(trip, Seat.STAY, MessageType.STAY_PICK, payload, "Stay pick")


def preference_learned(
    trip: Message, key: str = "eats_early", value=True, evidence: str = "asked for 18:00 dinners"
) -> Message:
    return _send(
        trip,
        Seat.RESTAURANT,
        MessageType.PREFERENCE_LEARNED,
        PreferenceLearnedPayload(key=key, value=value, evidence=evidence),
        "Learned a preference",
    )


def verdict(
    trip: Message,
    status: VerdictStatus,
    plan_hash: str | None = None,
    conflicts: Sequence[Conflict] = (),
    reply_to: str | None = None,
) -> Message:
    payload = VerdictPayload(status=status, plan_hash=plan_hash, conflicts=tuple(conflicts))
    return _send(trip, Seat.VERIFIER, MessageType.VERDICT, payload, f"Verdict: {status.value}", reply_to=reply_to)


def revised_dinner(
    item_id: str = "rest-late-dosa", name: str = "Late Night Dosa", start: str = "19:00", end: str = "20:15"
) -> ProposalItem:
    return demo.demo_restaurant_item().model_copy(
        update={"item_id": item_id, "name": name, "when": When(date=demo.at(17, 19).date(), start=start, end=end)}
    )


def error_message(trip: Message, sender: Seat, bad_message_id: str, code: str = "validation_failed") -> Message:
    return _send(
        trip, sender, MessageType.ERROR, ErrorPayload(code=code, bad_message_id=bad_message_id), "Rejected a message"
    )


class Recorder:
    """Wraps a seat and keeps everything it was sent and everything it sent back."""

    def __init__(self, seat: ConciergeSeat) -> None:
        self.seat = seat
        self.sent: list[Outgoing] = []
        self.received: list[Message] = []

    def say(self, text: str, sender: str = PERSON) -> list[Outgoing]:
        out = list(self.seat.handle_text(sender, text))
        self.sent.extend(out)
        return out

    def feed(self, message: Message) -> list[Outgoing]:
        self.received.append(message)
        out = list(self.seat.handle(message))
        self.sent.extend(out)
        return out

    def feed_all(self, incoming: Iterable[Message]) -> list[Outgoing]:
        out: list[Outgoing] = []
        for message in incoming:
            out = self.feed(message)
        return out


class Flow:
    """Drives the demo trip through the Concierge one stage at a time; each method returns what the seat sent."""

    def __init__(self, seat: ConciergeSeat, text: str = "plan the demo trip") -> None:
        self.rec = Recorder(seat)
        self.start_out = self.rec.say(text)
        self.trip: Message | None = None

    def geocode(self) -> list[Outgoing]:
        requests = [m for m in messages(self.start_out, MessageType.ROUTE_REQUEST, Seat.ROUTING)]
        out = self.rec.feed_all(geocode_reply(r) for r in requests)
        self.trip = messages(out, MessageType.REQUEST)[0]
        self.requests_out = out
        return out

    def answer_specialists(
        self,
        shortlist: Sequence[StayOption] | None = None,
        restaurant: Sequence[ProposalItem] | None = None,
        gym: Sequence[ProposalItem] | None = None,
        *,
        gym_answers: bool = True,
    ) -> list[Outgoing]:
        """Stay's shortlist, then the Restaurant's and the Gym's proposals. Returns the output of the last message."""
        incoming = [stay_shortlist(self.trip, *(shortlist if shortlist is not None else (stay(),)))]
        incoming.append(
            proposal(
                self.trip, Seat.RESTAURANT, *(restaurant if restaurant is not None else (demo.demo_restaurant_item(),))
            )
        )
        if gym_answers:
            incoming.append(proposal(self.trip, Seat.GYM, *(gym if gym is not None else (demo.demo_gym_item(),))))
        return self.rec.feed_all(incoming)

    def answer_legs(self, out: Sequence[Outgoing], minutes: dict[Mode, float | None] | None = None) -> list[Outgoing]:
        requests = messages(out, MessageType.ROUTE_REQUEST, Seat.ROUTING)
        return self.rec.feed_all(route_reply(r, minutes) for r in requests)

    def to_review(self, **specialists) -> Message:
        """Run the demo trip up to the merged plan going to the Verifier; returns that proposal."""
        self.geocode()
        out = self.answer_specialists(**specialists)
        out = self.answer_legs(out)
        (sent,) = messages(out, MessageType.PROPOSAL, Seat.VERIFIER)
        return sent


# --- shared helpers for the seat tests ---------------------------------------------------------


def make_seat(memory=None, llm=None, **kwargs) -> ConciergeSeat:
    return ConciergeSeat(memory if memory is not None else FakeMemory(), llm, **kwargs)


def deliver(flow: Flow, status=VerdictStatus.CLEAR, conflicts=()):
    """Run the demo to the Verifier and answer with a verdict on the exact plan it was sent."""
    sent = flow.to_review()
    out = flow.rec.feed(verdict(flow.trip, status, sent.payload.state.plan_hash(), conflicts))
    return sent, out


DINNER_CONFLICT = Conflict(
    rule="open_during_slot",
    item_id="rest-urban-vegan-kitchen",
    detail="Closes at 18:00 on Tue.",
    suggestion="Pick a place open until 20:00.",
)


def foreign_message() -> Message:
    """A valid message for a trip the Concierge never started."""
    return build_message(
        type=MessageType.ERROR,
        sender=Seat.STAY,
        to=[Seat.CONCIERGE],
        trip_id="no-such-trip",
        user_id="u",
        state_version=0,
        summary="x",
        payload=ErrorPayload(code="x"),
    )


def conflict_message(trip: Message) -> Message:
    """A `conflict` addressed to the Concierge, which only its owner should get."""
    return build_message(
        type=MessageType.CONFLICT,
        sender=Seat.VERIFIER,
        to=[Seat.CONCIERGE],
        trip_id=trip.trip_id,
        user_id=trip.user_id,
        state_version=0,
        summary="A conflict",
        payload=DINNER_CONFLICT,
    )


def error_for(trip: Message, sender: Seat, request: Message, code: str = "validation_failed") -> Message:
    return error_message(trip, sender, request.message_id, code)
