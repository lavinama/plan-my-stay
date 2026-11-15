"""Stand-in Stay, Restaurant and Gym seats that answer with fixture data.

docs/PLAN.md has each owner ship a stub first so the Concierge is built against it and swapped to
live data later. These are Mario's rehearsal stand-ins, not the owners' agents: they are stateless,
demo-specific (the New York trip) and exist so the whole swarm can run end to end without keys.

The Restaurant stub's first pick closes before dinner ends, so the Verifier's block fires every
time on fixtures; it replaces the pick when the Verifier sends it a `conflict`.

The Concierge ranks hotels from what each specialist reports about them (plan_my_stay.fit): the Stay stub ranks
its stays by average commute, and on a `hotel_query` the Gym stub reports the nearest gym to each hotel and the
Restaurant stub the average rank of the restaurants within a walk. The fixtures are placed so the answers tell a
story: commute alone puts TriBeCa ahead of the Financial District hotel, but TriBeCa's gym is a trek away and its
few restaurants rank low, so the Financial District hotel overtakes it. Distances come from plan_my_stay.geo.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Mode, Seat
from plan_my_stay.fit import (
    GymFitEntry,
    HotelPoint,
    NearbyGym,
    NearbyRestaurant,
    RestaurantFitEntry,
)
from plan_my_stay.geo import haversine_m, walk_minutes, within_walk
from plan_my_stay.messages import Message, build_message
from plan_my_stay.messages.payloads import (
    GymFitPayload,
    HotelQueryPayload,
    PreferenceLearnedPayload,
    ProposalPayload,
    RequestPayload,
    RestaurantFitPayload,
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


def _commute(b: tuple[float, float, float], c: tuple[float, float, float]) -> dict[str, dict[Mode, float | None]]:
    def row(t: tuple[float, float, float]) -> dict[Mode, float | None]:
        return {Mode.WALKING: t[0], Mode.TRANSIT: t[1], Mode.DRIVING: t[2]}

    return {"B": row(b), "C": row(c)}


_COMMUTE_ANCHORS = ("B", "C")  # the meetings that count for the stay


@dataclass(frozen=True)
class _Stay:
    stay_id: str
    zone_id: str
    name: str
    total_price: float
    cost_basis: str  # a CostBasis constant (CostBasis is a class of string constants, not a type)
    commute_minutes: dict[str, dict[Mode, float | None]]

    @property
    def average_commute_minutes(self) -> float:
        """The mean over meetings B and C of the fastest mode's minutes: what the Stay agent ranks by."""
        return mean(min(m for m in self.commute_minutes[anchor].values() if m is not None) for anchor in _COMMUTE_ANCHORS)


# Invented stays. Three are within the $1,000 demo budget and one is over it (the Stay agent drops it). By average
# commute: Chelsea 11.5 min, TriBeCa 12.5, Financial District 13.0; the specialists' fixtures below reorder the last two.
_STAYS = (
    _Stay("stay-chelsea-1", "z1", "Quiet Chelsea studio", 640.0, CostBasis.CARD_TOTAL_FEES_UNKNOWN, _commute((21, 12, 9), (38, 22, 14))),
    _Stay("stay-tribeca-1", "z2", "TriBeCa loft", 780.0, CostBasis.TOTAL_INCL_FEES, _commute((48, 24, 17), (15, 11, 8))),
    _Stay("stay-fidi-2", "z3", "Financial District hotel", 905.0, CostBasis.TOTAL_INCL_FEES, _commute((55, 28, 20), (12, 9, 6))),
    _Stay("stay-fidi-1", "z3", "Financial District penthouse", 1200.0, CostBasis.TOTAL_INCL_FEES, _commute((58, 30, 22), (13, 10, 7))),
)


class StayStub:
    """Answers a stay request with the in-budget stays ranked by average commute, and a stay pick for the others."""

    def handle(self, message: Message) -> list[Message]:
        if message.type is not MessageType.REQUEST:
            return []  # zone_fit, workout_slot, conflicts: the stub doesn't re-rank
        request: RequestPayload = message.payload
        limit = request.budget.amount if request.budget else float("inf")
        within = sorted((s for s in _STAYS if s.total_price <= limit), key=lambda s: s.average_commute_minutes)
        stays = tuple(
            StayOption(
                rank=i, deciding_factor="commute", stay_id=s.stay_id, zone_id=s.zone_id, name=s.name, total_price=s.total_price,
                cost_basis=s.cost_basis, commute_minutes=s.commute_minutes, average_commute_minutes=s.average_commute_minutes,
            )
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


@dataclass(frozen=True)
class _Gym:
    item_id: str
    name: str
    lat: float
    lng: float


@dataclass(frozen=True)
class _Restaurant:
    item_id: str
    name: str
    rank: int  # this stub's own fixed rank: 1 is the best fit for the user
    lat: float
    lng: float


_GYM_REACH_M = 3_000.0  # farther than this and a hotel has no nearest gym worth reporting
_TOP_RESTAURANTS = 3  # how many of the best-ranked nearby restaurants a restaurant_fit entry lists

# Hotels are measured from their zone's centre: Chelsea (40.747, -74.000), TriBeCa (40.7194, -74.0055) and the
# Financial District (40.7085, -74.0092). Nearest gym from each: Chelsea 316 m (Example HIIT Studio), the Financial
# District 350 m and TriBeCa 969 m (both Wall Street Strength Club): TriBeCa's gym is a trek.
_GYMS = (
    _Gym("gym-example-hiit", "Example HIIT Studio", 40.7488, -73.9971),
    _Gym("gym-wall-street-strength", "Wall Street Strength Club", 40.7115, -74.0105),
    _Gym("gym-union-square-iron-works", "Union Square Iron Works", 40.7359, -73.9911),
    _Gym("gym-midtown-climb-and-cycle", "Midtown Climb and Cycle", 40.7580, -73.9855),
)

# Within a 10-minute walk (615 m in a straight line) of each zone centre: Chelsea has ranks 1, 2 and 5 (average 2.67),
# the Financial District 3, 4 and 6 (4.33) and TriBeCa only 7 and 8 (7.5). The clusters are over 1.2 km apart.
_RESTAURANTS = (
    _Restaurant("rest-urban-vegan-kitchen", "Urban Vegan Kitchen", 1, 40.7445, -73.9983),
    _Restaurant("rest-saffron-and-thyme", "Saffron and Thyme", 2, 40.7490, -74.0040),
    _Restaurant("rest-battery-mezze-house", "Battery Mezze House", 3, 40.7062, -74.0105),
    _Restaurant("rest-pearl-street-dosa", "Pearl Street Dosa", 4, 40.7110, -74.0070),
    _Restaurant("rest-tandoor-garden", "Tandoor Garden", 5, 40.7440, -74.0030),
    _Restaurant("rest-bowling-green-falafel", "Bowling Green Falafel", 6, 40.7050, -74.0120),
    _Restaurant("rest-duane-street-diner", "Duane Street Diner", 7, 40.7212, -74.0030),
    _Restaurant("rest-hudson-street-steakhouse", "Hudson Street Steakhouse", 8, 40.7225, -74.0080),
)


def _nearest_gym(hotel: HotelPoint) -> NearbyGym | None:
    distance, gym = min(((haversine_m(hotel.lat, hotel.lng, g.lat, g.lng), g) for g in _GYMS), key=lambda pair: pair[0])
    if distance > _GYM_REACH_M:
        return None
    return NearbyGym(item_id=gym.item_id, name=gym.name, distance_m=distance, walk_minutes=walk_minutes(distance), lat=gym.lat, lng=gym.lng)


def _restaurants_near(hotel: HotelPoint, radius_minutes: float) -> RestaurantFitEntry:
    distances = ((r, haversine_m(hotel.lat, hotel.lng, r.lat, r.lng)) for r in _RESTAURANTS)
    near = sorted(((r, d) for r, d in distances if within_walk(d, radius_minutes)), key=lambda pair: pair[0].rank)
    top = tuple(
        NearbyRestaurant(item_id=r.item_id, name=r.name, rank=r.rank, walk_minutes=walk_minutes(d)) for r, d in near[:_TOP_RESTAURANTS]
    )
    average = mean(r.rank for r, _ in near) if near else None
    return RestaurantFitEntry(stay_id=hotel.stay_id, count=len(near), average_rank=average, top=top)


class RestaurantStub:
    def __init__(self, *, first_pick_closes_early: bool = True) -> None:
        self._first_pick_closes_early = first_pick_closes_early

    def handle(self, message: Message) -> list[Message]:
        if message.type is MessageType.CONFLICT:
            return [self._proposal(message, demo.demo_restaurant_item(), "Replacement dinner option")]
        if message.type is MessageType.HOTEL_QUERY:
            return [self._restaurant_fit(message)]
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
    def _restaurant_fit(query: Message) -> Message:
        payload: HotelQueryPayload = query.payload
        entries = tuple(_restaurants_near(hotel, payload.radius_minutes) for hotel in payload.hotels)
        with_some = sum(1 for e in entries if e.count > 0)
        return _reply(query, MessageType.RESTAURANT_FIT, Seat.RESTAURANT, [Seat.CONCIERGE],
                      f"Restaurants within a {payload.radius_minutes:g}-minute walk: {with_some} of {len(entries)} hotels have some",
                      RestaurantFitPayload(radius_minutes=payload.radius_minutes, hotels=entries))

    @staticmethod
    def _zone_fit(request: Message, payload: RequestPayload) -> Message:
        zone = payload.zones[0].id if payload.zones else "z1"
        item = ZoneFitItem(item_id="rest-urban-vegan-kitchen", name="Urban Vegan Kitchen", walk_minutes=6, fits=True)
        return _reply(request, MessageType.ZONE_FIT, Seat.RESTAURANT, [Seat.STAY], "7 vegetarian options within a short walk",
                      ZoneFitPayload(zone_id=zone, counts=ZoneFitCounts(restaurants=7), top_items=(item,)))


class GymStub:
    """Posts Tuesday's workout after meeting B (the 07:00 the user prefers is impossible on check-in day), and tells
    the Concierge the nearest gym to each candidate hotel."""

    def handle(self, message: Message) -> list[Message]:
        if message.type is MessageType.HOTEL_QUERY:
            return [self._gym_fit(message)]
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

    @staticmethod
    def _gym_fit(query: Message) -> Message:
        payload: HotelQueryPayload = query.payload
        entries = tuple(GymFitEntry(stay_id=hotel.stay_id, nearest_gym=_nearest_gym(hotel)) for hotel in payload.hotels)
        found = sum(1 for e in entries if e.nearest_gym is not None)
        return _reply(query, MessageType.GYM_FIT, Seat.GYM, [Seat.CONCIERGE],
                      f"Nearest gym within {_GYM_REACH_M / 1000:g} km: {found} of {len(entries)} hotels",
                      GymFitPayload(hotels=entries))
