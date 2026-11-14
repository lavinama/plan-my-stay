"""Stand-in Stay, Restaurant and Gym seats that answer with fixture data.

docs/PLAN.md has each owner ship a stub first so the Concierge is built against it and swapped to
live data later. These are Mario's rehearsal stand-ins, not the owners' agents: they are stateless,
demo-specific (the New York trip) and exist so the whole swarm can run end to end without keys.

The Restaurant stub's first pick closes before dinner ends, so the Verifier's block fires every
time on fixtures; it replaces the pick when the Verifier sends it a `conflict`.
"""

import datetime as dt
from collections.abc import Sequence

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Mode, Seat
from plan_my_stay.messages import Message, build_message
from plan_my_stay.messages.payloads import (
    PreferenceLearnedPayload,
    ProposalPayload,
    RequestPayload,
    StayPickPayload,
    StayShortlistPayload,
    WorkoutPlace,
    WorkoutSlotPayload,
    ZoneFitCounts,
    ZoneFitItem,
    ZoneFitPayload,
)
from plan_my_stay.models import CostBasis, Slot, StayOption, Usage, When

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_USAGE = Usage(calls=3, credits=118)


def _weekday(day: dt.date) -> str:
    return _DAYS[day.weekday()]


def _reply(request: Message, type_: MessageType, sender: Seat, to: Sequence[Seat], summary: str, payload: object) -> Message:
    return build_message(
        type=type_, sender=sender, to=list(to), trip_id=request.trip_id, user_id=request.user_id,
        state_version=request.state_version, summary=summary, payload=payload, reply_to=request.message_id,
    )


def _in_roster(request: RequestPayload, *seats: Seat) -> list[Seat]:
    """An empty roster means everyone, so the stubs also work when a test sends a bare request."""
    return [s for s in seats if not request.roster or s in request.roster]


def _first_slot(request: RequestPayload, kind: str) -> Slot | None:
    return next((s for s in request.slots if s.kind == kind and s.date == request.dates.check_in), None)


def _commute(b: tuple[float, float, float], c: tuple[float, float, float]) -> dict[str, dict[Mode, float]]:
    def row(t: tuple[float, float, float]) -> dict[Mode, float]:
        return {Mode.WALKING: t[0], Mode.TRANSIT: t[1], Mode.DRIVING: t[2]}

    return {"B": row(b), "C": row(c)}


# Invented stays: the cheapest first, one over the demo budget (the Stay agent drops it).
_STAYS = (
    ("stay-chelsea-1", "z1", "Quiet Chelsea studio", 640.0, CostBasis.CARD_TOTAL_FEES_UNKNOWN, "commute", _commute((21, 12, 9), (38, 22, 14))),
    ("stay-tribeca-1", "z2", "TriBeCa loft", 780.0, CostBasis.TOTAL_INCL_FEES, "gym_fit", _commute((48, 24, 17), (15, 11, 8))),
    ("stay-fidi-1", "z3", "Financial District penthouse", 1200.0, CostBasis.TOTAL_INCL_FEES, "restaurant_fit", _commute((55, 28, 20), (12, 9, 6))),
)


class StayStub:
    """Answers a stay request with a ranked shortlist within the budget and a stay pick for the others."""

    def handle(self, message: Message) -> list[Message]:
        if message.type is not MessageType.REQUEST:
            return []  # zone_fit, workout_slot, conflicts: the stub doesn't re-rank
        request: RequestPayload = message.payload
        limit = request.budget.amount if request.budget else float("inf")
        within = [s for s in _STAYS if s[3] <= limit]
        stays = tuple(
            StayOption(rank=i, deciding_factor=s[5], stay_id=s[0], zone_id=s[1], name=s[2], total_price=s[3], cost_basis=s[4], commute_minutes=s[6])
            for i, s in enumerate(within, start=1)
        )
        out = [_reply(message, MessageType.STAY_SHORTLIST, Seat.STAY, [Seat.CONCIERGE],
                      f"{len(stays)} stays within the budget", StayShortlistPayload(stays=stays))]
        if stays:
            top = stays[0]
            peers = [*_in_roster(request, Seat.RESTAURANT, Seat.GYM), Seat.CONCIERGE]
            out.append(_reply(message, MessageType.STAY_PICK, Seat.STAY, peers, f"Stay pick: {top.name}",
                              StayPickPayload(stay_id=top.stay_id, zone_id=top.zone_id, commute_minutes=top.commute_minutes)))
        return out


class RestaurantStub:
    def __init__(self, *, first_pick_closes_early: bool = True) -> None:
        self._first_pick_closes_early = first_pick_closes_early

    def handle(self, message: Message) -> list[Message]:
        if message.type is MessageType.CONFLICT:
            return [self._proposal(message, demo.demo_restaurant_item(), "Replacement dinner option")]
        if message.type is not MessageType.REQUEST:
            return []
        request: RequestPayload = message.payload
        slot = _first_slot(request, "dinner")
        out = [self._zone_fit(message, request)]
        if slot is not None:
            item = demo.demo_restaurant_item().model_copy(update={"slot_id": slot.id})
            if self._first_pick_closes_early:
                item = item.model_copy(update={
                    "item_id": "rest-early-closer-cafe", "name": "Early Closer Cafe", "dedupe_key": "early-closer-cafe|10011",
                    "hours": {_weekday(slot.date): "11:00-19:00"},
                })
            out.append(self._proposal(message, item, f"1 dinner option for {slot.date:%a %d %b}"))
        learned = _reply(message, MessageType.PREFERENCE_LEARNED, Seat.RESTAURANT,
                         [Seat.CONCIERGE, *_in_roster(request, Seat.GYM)], "Learned: the user eats early",
                         PreferenceLearnedPayload(key="meal_timing", value="early", evidence="Prefers dinner before 19:00"))
        return [*out, learned]

    @staticmethod
    def _proposal(request: Message, item, summary: str) -> Message:
        return _reply(request, MessageType.PROPOSAL, Seat.RESTAURANT, [Seat.CONCIERGE, Seat.VERIFIER], summary,
                      ProposalPayload(agent=Seat.RESTAURANT, items=(item,), usage=_USAGE))

    @staticmethod
    def _zone_fit(request: Message, payload: RequestPayload) -> Message:
        zone = payload.zones[0].id if payload.zones else "z1"
        item = ZoneFitItem(item_id="rest-urban-vegan-kitchen", name="Urban Vegan Kitchen", walk_minutes=6, fits=True)
        return _reply(request, MessageType.ZONE_FIT, Seat.RESTAURANT, [Seat.STAY], "7 vegetarian options within a short walk",
                      ZoneFitPayload(zone_id=zone, counts=ZoneFitCounts(restaurants=7), top_items=(item,)))


class GymStub:
    """Posts Tuesday's workout after meeting B: the 07:00 the user prefers is impossible on check-in day."""

    def handle(self, message: Message) -> list[Message]:
        if message.type is not MessageType.REQUEST:
            return []
        request: RequestPayload = message.payload
        slot = _first_slot(request, "workout")
        zone = request.zones[0].id if request.zones else "z1"
        out = [_reply(message, MessageType.ZONE_FIT, Seat.GYM, [Seat.STAY], "HIIT gym within a short walk",
                      ZoneFitPayload(zone_id=zone, counts=ZoneFitCounts(gyms=2),
                                     top_items=(ZoneFitItem(item_id="gym-example-hiit", name="Example HIIT Studio", walk_minutes=4, fits=True),)))]
        if slot is None:
            return out
        gym = demo.demo_gym_item().model_copy(update={"slot_id": slot.id})
        place = WorkoutPlace(name=gym.name, address=gym.address, lat=gym.lat, lng=gym.lng)
        when: When = gym.when
        out.append(_reply(message, MessageType.WORKOUT_SLOT, Seat.GYM, [*_in_roster(request, Seat.RESTAURANT), Seat.CONCIERGE],
                          f"Workout {when.date:%a %d %b} {when.start} to {when.end}",
                          WorkoutSlotPayload(date=when.date, start=when.start, end=when.end, place=place)))
        out.append(_reply(message, MessageType.PROPOSAL, Seat.GYM, [Seat.CONCIERGE, Seat.VERIFIER], "1 workout option",
                          ProposalPayload(agent=Seat.GYM, items=(gym,), usage=_USAGE)))
        return out
