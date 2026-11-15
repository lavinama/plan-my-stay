"""Building the trip state and the merged plan: pure functions the seat calls.

Items are merged in each specialist's own order. With the Stay agent as the only specialist the meetings go to it
as `Meeting`s (address and time) and the Concierge builds no anchors.
"""

import datetime as dt
from collections.abc import Mapping, Sequence

from plan_my_stay.concierge.intake import TripIntake
from plan_my_stay.concierge.slots import build_slots
from plan_my_stay.concierge.zones import ZoneProvider
from plan_my_stay.enums import Seat
from plan_my_stay.messages.payloads import ProposalPayload
from plan_my_stay.models import (
    Anchor,
    Conflict,
    LatLng,
    Meeting,
    PlanEntry,
    Profile,
    Slot,
)
from plan_my_stay.state import TripState
from plan_my_stay.timeutil import TRIP_TZ

_MEAL_KINDS = frozenset({"breakfast", "brunch", "lunch", "dinner", "snack"})
_PROFILE_FIELDS: Mapping[Seat, tuple[str, ...]] = {
    Seat.STAY: ("stay_preferences", "price_comfort", "transport_mode"),
    Seat.RESTAURANT: ("diet", "cuisines", "price_comfort"),
    Seat.GYM: ("gym_types", "workout_times"),
}


def build_anchors(intake: TripIntake, located: Mapping[str, LatLng], check_in_time: dt.time) -> tuple[Anchor, ...]:
    """One anchor per meeting. `counts_for_stay` is false for a meeting that starts before check-in: it doesn't
    constrain where the stay is. Meeting addresses and names are left behind."""
    check_in = dt.datetime.combine(intake.check_in, check_in_time, tzinfo=TRIP_TZ)
    return tuple(
        Anchor(
            id=meeting.id,
            lat=located[meeting.id].lat,
            lng=located[meeting.id].lng,
            start=meeting.start,
            end=meeting.end,
            counts_for_stay=meeting.start >= check_in,
        )
        for meeting in intake.meetings
    )


def build_meetings(intake: TripIntake, check_in_time: dt.time) -> tuple[Meeting, ...]:
    """One `Meeting` per meeting, for a Stay agent that finds the coordinates itself. Address and time only: never a
    title or attendees. `counts_for_stay` is false for a meeting that starts before check-in, as for an anchor."""
    check_in = dt.datetime.combine(intake.check_in, check_in_time, tzinfo=TRIP_TZ)
    return tuple(
        Meeting(id=m.id, address=m.address, start=m.start, end=m.end, counts_for_stay=m.start >= check_in)
        for m in intake.meetings
    )


def slots_for_trip(intake: TripIntake, profile: Profile) -> tuple[Slot, ...]:
    """The intake's slots; the workout windows follow the remembered preferred time (the demo keeps its own)."""
    if intake.is_demo:
        return intake.slots
    preferred = profile.workout_times[0] if profile.workout_times else None
    return build_slots(
        intake.check_in, intake.check_out, wants_workouts=intake.wants_workouts, preferred_workout_start=preferred
    )


def build_state(
    *,
    trip_id: str,
    user_id: str,
    intake: TripIntake,
    anchors: Sequence[Anchor],
    profile: Profile,
    zones: ZoneProvider,
    roster: Sequence[Seat] | None = None,
) -> TripState:
    """The roster is every specialist the trip needs, unless the caller names the seats (the Stay agent alone)."""
    if roster is None:
        roster = (Seat.STAY, Seat.RESTAURANT, *((Seat.GYM,) if intake.wants_workouts else ()))
    return TripState(
        trip_id=trip_id,
        user_id=user_id,
        city=intake.city,
        check_in=intake.check_in,
        check_out=intake.check_out,
        guests=intake.guests,
        anchors=tuple(anchors),
        zones=zones.zones_for(intake.city, anchors),
        slots=slots_for_trip(intake, profile),
        budget=intake.budget,
        profile=profile,
        roster=tuple(roster),
    )


def profile_for(seat: Seat, profile: Profile) -> Profile:
    """Only the profile fields a seat needs, plus the preferences agents learned at run time (the extra keys)."""
    known = {name: getattr(profile, name) for name in _PROFILE_FIELDS.get(seat, ())}
    return Profile.model_validate({**known, **(profile.model_extra or {})})


def slots_for(seat: Seat, slots: Sequence[Slot]) -> tuple[Slot, ...]:
    """Restaurants get the meal slots, the gym gets the workouts, the Stay agent needs none."""
    if seat is Seat.RESTAURANT:
        return tuple(s for s in slots if s.kind in _MEAL_KINDS)
    if seat is Seat.GYM:
        return tuple(s for s in slots if s.kind == "workout")
    return ()


def merge_entries(proposals: Mapping[Seat, ProposalPayload], roster: Sequence[Seat]) -> tuple[PlanEntry, ...]:
    """The chosen items: each specialist's items in its own order, the first per slot (the specialist ranked it)."""
    entries: list[PlanEntry] = []
    taken: set[tuple[Seat, str]] = set()
    for seat in roster:
        if seat is Seat.STAY or seat not in proposals:
            continue
        for item in proposals[seat].items:
            key = (seat, item.slot_id or f"item:{item.item_id}")
            if key in taken:
                continue
            taken.add(key)
            entries.append(PlanEntry(agent=seat, item=item))
    return tuple(entries)


# Rules the Verifier answers with `warn`: something it cannot check, or a note. Nobody is asked to revise these
# (SEAT_PROTOCOL section 4). A `block` verdict can still list them next to the blocking conflicts.
WARN_RULES = frozenset({"unknown_hours", "missing_leg", "fee_basis_unknown"})


def conflict_owners(conflicts: Sequence[Conflict], state: TripState) -> frozenset[Seat]:
    """The seats that must revise: the owner of each blocking conflict. Stay and budget conflicts go to Stay.

    Warn-level conflicts are skipped: the Verifier sends no `conflict` message for them, so no one will answer."""
    blocking = [c for c in conflicts if c.rule not in WARN_RULES] or list(conflicts)
    by_item = {entry.item.item_id: entry.agent for entry in state.entries}
    return frozenset(by_item.get(conflict.item_id, Seat.STAY) for conflict in blocking)
