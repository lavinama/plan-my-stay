"""The New York demo trip as data: the same facts docs/PLAN.md uses, for stubs, examples and the rehearsal.

Coordinates and zone boxes are fixture values (approximate for meeting C and the zones);
the Routing seat geocodes real addresses live. Tue 17 Nov to Thu 19 Nov 2026, one traveler.
"""

import datetime as dt

from plan_my_stay.enums import Mode, Seat
from plan_my_stay.models import (
    Address,
    Anchor,
    Bounds,
    Budget,
    Cost,
    CostBasis,
    Fit,
    LatLng,
    Leg,
    PlanEntry,
    Profile,
    ProposalItem,
    Reader,
    Slot,
    Source,
    StayOption,
    When,
    Window,
    Zone,
)
from plan_my_stay.state import TripState
from plan_my_stay.timeutil import TRIP_TZ

TRIP_ID = "7c3f1d2e-5a4b-4e8a-9b6d-2f1c0a8e4b73"
USER_ID = "demo-user-1"
CITY = "New York, NY"

# Meeting addresses stay with the Concierge; agents only ever see anchors.
MEETING_ADDRESSES = {
    "A": "199 Water St, New York, NY 10038",
    "B": "368 9th Ave, New York, NY 10001",
    "C": "524 Broadway, New York, NY 10012",
}


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    """A moment in Nov 2026 in the trip's time zone."""
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=TRIP_TZ)


def demo_anchors() -> tuple[Anchor, ...]:
    return (
        Anchor(id="A", lat=40.7066, lng=-74.0053, start=at(17, 11), end=at(17, 12), counts_for_stay=False),
        Anchor(id="B", lat=40.7502, lng=-73.9987, start=at(17, 16), end=at(17, 17), counts_for_stay=True),
        Anchor(id="C", lat=40.7234, lng=-73.9991, start=at(18, 10), end=at(18, 11), counts_for_stay=True),
    )


def demo_zones() -> tuple[Zone, ...]:
    return (
        Zone(id="z1", name="Chelsea", bounds=Bounds(ne=LatLng(lat=40.756, lng=-73.99), sw=LatLng(lat=40.738, lng=-74.01))),
        Zone(id="z2", name="TriBeCa", bounds=Bounds(ne=LatLng(lat=40.7262, lng=-73.9985), sw=LatLng(lat=40.7125, lng=-74.0125))),
        Zone(
            id="z3",
            name="Financial District",
            bounds=Bounds(ne=LatLng(lat=40.7160, lng=-73.9985), sw=LatLng(lat=40.7010, lng=-74.0200)),
        ),
    )


def demo_slots() -> tuple[Slot, ...]:
    """Lunch, dinner and one workout on Tue and Wed. s1 is Tuesday dinner, as in the plan's example request."""
    d17, d18 = dt.date(2026, 11, 17), dt.date(2026, 11, 18)
    return (
        Slot(id="s1", kind="dinner", date=d17, window=Window(start="17:30", end="21:00")),
        Slot(id="s2", kind="lunch", date=d17, window=Window(start="12:15", end="15:00")),
        Slot(id="s3", kind="workout", date=d17, window=Window(start="17:00", end="20:00")),
        Slot(id="s4", kind="workout", date=d18, window=Window(start="07:00", end="08:30")),
        Slot(id="s5", kind="lunch", date=d18, window=Window(start="11:30", end="14:30")),
        Slot(id="s6", kind="dinner", date=d18, window=Window(start="17:30", end="21:00")),
    )


def demo_profile() -> Profile:
    """Invented values for the demo; real preferences replace them later."""
    return Profile(
        diet=("vegetarian",),
        cuisines=("Mediterranean", "Indian"),
        gym_types=("hiit",),
        workout_times=("07:00",),
        stay_preferences=("quiet street", "private bathroom"),
    )


def demo_state() -> TripState:
    """The trip as the Concierge holds it right after the user's request: no stay, no plan yet."""
    return TripState(
        trip_id=TRIP_ID,
        user_id=USER_ID,
        city=CITY,
        check_in=dt.date(2026, 11, 17),
        check_out=dt.date(2026, 11, 19),
        anchors=demo_anchors(),
        zones=demo_zones(),
        slots=demo_slots(),
        budget=Budget(currency="USD", amount=1000),
        profile=demo_profile(),
        roster=(Seat.STAY, Seat.RESTAURANT, Seat.GYM),
    )


def demo_stay(total_price: float = 640.0, cost_basis: str = CostBasis.CARD_TOTAL_FEES_UNKNOWN) -> StayOption:
    return StayOption(
        rank=1,
        deciding_factor="commute",
        stay_id="stay-chelsea-1",
        zone_id="z1",
        name="Quiet Chelsea studio",
        total_price=total_price,
        currency="USD",
        cost_basis=cost_basis,
        commute_minutes={
            "B": {Mode.WALKING: 21.0, Mode.TRANSIT: 12.0, Mode.DRIVING: 9.0},
            "C": {Mode.WALKING: 38.0, Mode.TRANSIT: 22.0, Mode.DRIVING: 14.0},
        },
    )


def _source(url: str) -> Source:
    return Source(url=url, fetched_at=at(14, 16, 6))


def demo_restaurant_item() -> ProposalItem:
    """The plan's example proposal item: open until 22:00 on Tuesday, so it clears dinner at 18:30 to 19:45."""
    return ProposalItem(
        item_id="rest-urban-vegan-kitchen",
        slot_id="s1",
        name="Urban Vegan Kitchen",
        address=Address(street="265 W 23rd St", city="New York", zip="10011"),
        lat=40.7445,
        lng=-73.9983,
        when=When(date=dt.date(2026, 11, 17), start="18:30", end="19:45"),
        cost=Cost(currency="USD", amount=None, basis=CostBasis.UNKNOWN),
        fit=Fit(reason="Fully vegan menu; open until 22:00 on Tuesday", profile_fields=("diet",)),
        hours={"Tue": "11:00-22:00"},
        source=_source("https://urbanvegankitchen.com"),
        reader=Reader(model="Qwen/Qwen3.5-4B", escalated=False),
        dedupe_key="urban-vegan-kitchen|10011",
    )


def demo_gym_item() -> ProposalItem:
    """Tuesday's workout after meeting B (the 07:00 preference is impossible on check-in day)."""
    return ProposalItem(
        item_id="gym-example-hiit",
        slot_id="s3",
        name="Example HIIT Studio",
        address=Address(street="1 Example Plaza", city="New York", zip="10001"),
        lat=40.7488,
        lng=-73.9971,
        when=When(date=dt.date(2026, 11, 17), start="17:30", end="18:15"),
        cost=Cost(currency="USD", amount=35, basis=CostBasis.DAY_PASS),
        fit=Fit(reason="HIIT classes; 17:30 class on Tuesdays", profile_fields=("gym_types",)),
        hours={"Tue": "06:00-21:00"},
        source=_source("https://example-hiit.test/schedule"),
        reader=Reader(model="Qwen/Qwen3.5-4B", escalated=False),
        dedupe_key="example-hiit-studio|10001",
    )


def _leg(from_id: str, to_id: str, depart_at: dt.datetime, walking: float, transit: float, driving: float) -> Leg:
    """A leg with the fastest mode chosen, as the Concierge does (plain code, by travel time)."""
    minutes = {Mode.WALKING: walking, Mode.TRANSIT: transit, Mode.DRIVING: driving}
    return Leg(from_id=from_id, to_id=to_id, depart_at=depart_at, minutes_by_mode=minutes, chosen_mode=min(minutes, key=minutes.get))


def demo_legs() -> tuple[Leg, ...]:
    """Tuesday: A, then B, the workout, dinner and the stay; Wednesday: the stay to C. Every leg leaves after the
    previous event ends and arrives before the next one starts, so the Verifier clears the timing."""
    return (
        _leg("A", "B", at(17, 12), walking=70, transit=25, driving=20),
        _leg("B", "gym-example-hiit", at(17, 17, 5), walking=4, transit=8, driving=6),
        _leg("gym-example-hiit", "rest-urban-vegan-kitchen", at(17, 18, 20), walking=7, transit=9, driving=5),
        _leg("rest-urban-vegan-kitchen", "stay", at(17, 19, 50), walking=6, transit=6, driving=4),
        _leg("stay", "C", at(18, 9), walking=38, transit=22, driving=14),
    )


def demo_state_with_plan(stay_total: float = 640.0, stay_basis: str = CostBasis.CARD_TOTAL_FEES_UNKNOWN) -> TripState:
    """A complete, clean plan for the Verifier: stay, Tuesday workout and dinner, and the transport legs."""
    return demo_state().bump(
        stay=demo_stay(stay_total, stay_basis),
        entries=(
            PlanEntry(agent=Seat.GYM, item=demo_gym_item()),
            PlanEntry(agent=Seat.RESTAURANT, item=demo_restaurant_item()),
        ),
        legs=demo_legs(),
    )
