"""The Verifier's rules: pure functions from a `TripState` to tuples of `Conflict`.

The rule names and severities are the table in docs/SEAT_PROTOCOL.md section 4. Nothing here
mutates the state, calls out or reads a clock, so every function is safe to call twice and to
test on mutated copies of the demo plan.

Conventions a reader of a `Conflict` can rely on:
- `item_id` is the item the owner must fix: for `travel_time` and `missing_leg` the later of the
  two events (or the only event at the end of a leg to or from the stay); for the budget rules
  the stay's id.
- A leg between two events that goes through the stay (event to `"stay"`, then `"stay"` to the
  next event) counts as a leg between those two events.
"""

import datetime as dt
import logging
from collections.abc import Callable, Sequence
from types import MappingProxyType

from plan_my_stay.budget import check_budget
from plan_my_stay.enums import Seat, VerdictStatus
from plan_my_stay.models import Conflict, Leg
from plan_my_stay.state import TripState
from plan_my_stay.verifier.events import (
    STAY_ID,
    Event,
    EventKind,
    build_events,
    distance_km,
    event_index,
    fmt_span,
    fmt_time,
    overlaps,
)
from plan_my_stay.verifier.hours import is_open_during, weekday_key

logger = logging.getLogger(__name__)

MISSING_LEG_KM = 0.3  # consecutive events closer than this are the same place: no leg needed


class Rule:
    """Stable `Conflict.rule` names. Other seats may match on them."""

    OPEN_DURING_SLOT = "open_during_slot"
    UNKNOWN_HOURS = "unknown_hours"
    OUTSIDE_SLOT_WINDOW = "outside_slot_window"
    OVERLAPS_EVENT = "overlaps_event"
    TRAVEL_TIME = "travel_time"
    MISSING_LEG = "missing_leg"
    OVER_BUDGET = "over_budget"
    FEE_BASIS_UNKNOWN = "fee_basis_unknown"


SEVERITY = MappingProxyType(
    {
        Rule.OPEN_DURING_SLOT: VerdictStatus.BLOCK,
        Rule.UNKNOWN_HOURS: VerdictStatus.WARN,
        Rule.OUTSIDE_SLOT_WINDOW: VerdictStatus.BLOCK,
        Rule.OVERLAPS_EVENT: VerdictStatus.BLOCK,
        Rule.TRAVEL_TIME: VerdictStatus.BLOCK,
        Rule.MISSING_LEG: VerdictStatus.WARN,
        Rule.OVER_BUDGET: VerdictStatus.BLOCK,
        Rule.FEE_BASIS_UNKNOWN: VerdictStatus.WARN,
    }
)


def is_blocking(conflict: Conflict) -> bool:
    """Block or warn is a property of the rule. A rule this code does not know never blocks."""
    return SEVERITY.get(conflict.rule) is VerdictStatus.BLOCK


def _money(currency: str, amount: float) -> str:
    return f"{currency} {amount:,.2f}"


def _percent(fraction: float) -> str:
    return f"{fraction * 100:g}%"


# --- opening hours -------------------------------------------------------------------------------


def _hours_that_day(hours: dict, day: dt.date) -> str:
    """The day's own periods as text. Only called when the hours are known to have an entry for that weekday."""
    entry = hours.get(weekday_key(day), ())
    return entry if isinstance(entry, str) else (", ".join(entry) or "closed")


def check_hours(state: TripState) -> tuple[Conflict, ...]:
    """`open_during_slot` (block) when known hours do not cover the item's `when`; `unknown_hours` (warn) when they can't say."""
    conflicts: list[Conflict] = []
    for entry in state.entries:
        item, when = entry.item, entry.item.when
        if when is None:
            continue
        day = f"{weekday_key(when.date)} {when.date:%d %b}"
        verdict = is_open_during(item.hours, when.date, when.start, when.end)
        if verdict is False:
            conflicts.append(
                Conflict(
                    rule=Rule.OPEN_DURING_SLOT,
                    item_id=item.item_id,
                    detail=(
                        f"{item.name} is open {_hours_that_day(item.hours or {}, when.date)} on {day}, "
                        f"but it is planned {when.start}-{when.end}."
                    ),
                    suggestion=f"Pick a place open for the whole {when.start}-{when.end} slot, or move the slot inside its opening hours.",
                )
            )
        elif verdict is None:
            conflicts.append(
                Conflict(
                    rule=Rule.UNKNOWN_HOURS,
                    item_id=item.item_id,
                    detail=f"No opening hours are known for {item.name} on {day}, so it cannot be checked as open {when.start}-{when.end}.",
                    suggestion="Confirm the hours on the venue's own page before relying on this item.",
                )
            )
    return tuple(conflicts)


# --- slot windows --------------------------------------------------------------------------------


def check_slot_windows(state: TripState) -> tuple[Conflict, ...]:
    """`outside_slot_window` (block): the item's `when` is on another day than its slot or outside the slot's window."""
    slots = {slot.id: slot for slot in state.slots}
    conflicts: list[Conflict] = []
    for entry in state.entries:
        item, when = entry.item, entry.item.when
        if when is None or item.slot_id is None:
            continue
        slot = slots.get(item.slot_id)
        if slot is None:
            logger.warning("item %s names slot %s, which the plan does not have; skipping the window check", item.item_id, item.slot_id)
            continue
        inside = when.date == slot.date and slot.window.start <= when.start and when.end <= slot.window.end
        if not inside:
            conflicts.append(
                Conflict(
                    rule=Rule.OUTSIDE_SLOT_WINDOW,
                    item_id=item.item_id,
                    detail=(
                        f"{item.name} is planned {when.start}-{when.end} on {when.date}, but slot {slot.id} ({slot.kind}) "
                        f"allows {slot.window.start}-{slot.window.end} on {slot.date}."
                    ),
                    suggestion=f"Move it inside {slot.window.start}-{slot.window.end} on {slot.date}.",
                )
            )
    return tuple(conflicts)


# --- overlaps ------------------------------------------------------------------------------------


def check_overlaps(state: TripState) -> tuple[Conflict, ...]:
    """`overlaps_event` (block): an item shares time with an anchor, or with an item that starts no later.

    A pair of items is reported once, on the later-starting item, so the owner who must move gets the message.
    """
    events = build_events(state)
    conflicts: list[Conflict] = []
    for i, event in enumerate(events):
        if event.kind is not EventKind.ITEM:
            continue
        clashes = [
            other
            for j, other in enumerate(events)
            if j != i and overlaps(event, other) and (other.kind is EventKind.ANCHOR or j < i)
        ]
        if clashes:
            clash_text = " and ".join(f"{other.name} ({fmt_span(other)})" for other in clashes)
            conflicts.append(
                Conflict(
                    rule=Rule.OVERLAPS_EVENT,
                    item_id=event.id,
                    detail=f"{event.name} ({fmt_span(event)}) overlaps {clash_text}.",
                    suggestion="Move it to a time when nothing else is scheduled.",
                )
            )
    return tuple(conflicts)


# --- legs ----------------------------------------------------------------------------------------


def _chosen_minutes(leg: Leg) -> float | None:
    if leg.chosen_mode is None:
        return None
    return leg.minutes_by_mode.get(leg.chosen_mode)


def _later(origin: Event | None, destination: Event | None) -> Event:
    """The event a leg's conflict is filed against: the later-starting one (the destination on a tie), or the only one."""
    candidates = [event for event in (destination, origin) if event is not None]
    return max(candidates, key=lambda event: event.start)


def _leg_problems(leg: Leg, minutes: float, origin: Event | None, destination: Event | None) -> list[str]:
    problems: list[str] = []
    if origin is not None and leg.depart_at < origin.end:
        problems.append(f"it leaves at {fmt_time(leg.depart_at)} but {origin.name} runs until {fmt_time(origin.end)}")
    if destination is not None:
        arrival = leg.depart_at + dt.timedelta(minutes=minutes)
        if arrival > destination.start:
            problems.append(
                f"by {leg.chosen_mode} it takes {minutes:g} min, so leaving at {fmt_time(leg.depart_at)} it arrives "
                f"{fmt_time(arrival)}, after {destination.name} starts at {fmt_time(destination.start)}"
            )
    return problems


def check_travel_times(state: TripState) -> tuple[Conflict, ...]:
    """`travel_time` (block) for a leg that does not fit its gap; `missing_leg` (warn) for a leg with no usable chosen-mode minutes.

    A leg is valid when it departs no earlier than the from-event ends and arrives (departure plus the
    chosen mode's minutes) no later than the to-event starts. The stay has no time, so it adds no limit.
    """
    index = event_index(build_events(state))
    conflicts: list[Conflict] = []
    for leg in state.legs:
        origin, destination = index.get(leg.from_id), index.get(leg.to_id)
        for end_id, event in ((leg.from_id, origin), (leg.to_id, destination)):
            if event is None and end_id != STAY_ID:
                logger.warning("leg %s -> %s names %s, which is not an event in the plan", leg.from_id, leg.to_id, end_id)
        if origin is None and destination is None:
            continue
        target = _later(origin, destination)
        minutes = _chosen_minutes(leg)
        if minutes is None:
            conflicts.append(
                Conflict(
                    rule=Rule.MISSING_LEG,
                    item_id=target.id,
                    detail=f"The leg {leg.from_id} to {leg.to_id} has no travel time for its chosen mode, so it cannot be checked.",
                    suggestion="Ask Routing again, or choose a mode that has a route.",
                )
            )
            continue
        problems = _leg_problems(leg, minutes, origin, destination)
        if problems:
            conflicts.append(
                Conflict(
                    rule=Rule.TRAVEL_TIME,
                    item_id=target.id,
                    detail=f"The leg {leg.from_id} to {leg.to_id} does not fit: {'; '.join(problems)}.",
                    suggestion=f"Leave at least {minutes:g} min between them: pick a closer place, a faster mode or a later time.",
                )
            )
    return tuple(conflicts)


def _stay_neighbours(legs: Sequence[Leg]) -> frozenset[str]:
    """Event ids that have a leg to or from the stay."""
    ids = {leg.to_id if leg.from_id == STAY_ID else leg.from_id for leg in legs if STAY_ID in (leg.from_id, leg.to_id)}
    return frozenset(ids - {STAY_ID})


def check_missing_legs(state: TripState) -> tuple[Conflict, ...]:
    """`missing_leg` (warn): consecutive events more than 0.3 km apart (or of unknown place) with no leg between them.

    "Between them" is a leg either way round, or a leg from each to the stay.
    """
    events = build_events(state)
    direct = {(leg.from_id, leg.to_id) for leg in state.legs}
    via_stay = _stay_neighbours(state.legs)
    conflicts: list[Conflict] = []
    for earlier, later in zip(events, events[1:]):
        covered = (
            (earlier.id, later.id) in direct
            or (later.id, earlier.id) in direct
            or (earlier.id in via_stay and later.id in via_stay)
        )
        if covered:
            continue
        distance = distance_km(earlier, later)
        if distance is None:
            reason = f"the location of {earlier.name} or {later.name} is unknown"
        elif distance > MISSING_LEG_KM:
            reason = f"{later.name} is about {distance:.1f} km from {earlier.name}"
        else:
            continue
        conflicts.append(
            Conflict(
                rule=Rule.MISSING_LEG,
                item_id=later.id,
                detail=f"No leg between {earlier.name} ({fmt_span(earlier)}) and {later.name} ({fmt_span(later)}): {reason}.",
                suggestion="Ask Routing for the travel time between them so the gap can be checked.",
            )
        )
    return tuple(conflicts)


# --- budget --------------------------------------------------------------------------------------


def check_stay_budget(state: TripState, fee_reserve_pct: float) -> tuple[Conflict, ...]:
    """`over_budget` (block) when the stay's effective total exceeds the budget; `fee_basis_unknown` (warn) when a reserve was added.

    Only the stay counts against the budget for now. A stay priced in another currency than the budget cannot
    be compared, so it blocks as over budget rather than passing unchecked.
    """
    if fee_reserve_pct < 0:
        raise ValueError("fee_reserve_pct must not be negative")
    stay, budget = state.stay, state.budget
    if stay is None:
        return ()
    if stay.currency != budget.currency:
        return (
            Conflict(
                rule=Rule.OVER_BUDGET,
                item_id=stay.stay_id,
                detail=f"The stay is priced in {stay.currency} but the budget is in {budget.currency}, so it cannot be confirmed to be within budget.",
                suggestion=f"Ask the Stay agent for the price in {budget.currency}.",
            ),
        )
    result = check_budget(stay.total_price, stay.cost_basis, budget=budget.amount, fee_reserve_pct=fee_reserve_pct)
    total = _money(stay.currency, stay.total_price)
    effective = _money(stay.currency, result.effective_total)
    conflicts: list[Conflict] = []
    if not result.within_budget:
        priced = f"{total} plus a {_percent(fee_reserve_pct)} fee reserve ({effective})" if result.reserve_applied else total
        conflicts.append(
            Conflict(
                rule=Rule.OVER_BUDGET,
                item_id=stay.stay_id,
                detail=f"The stay costs {priced}, over the {_money(budget.currency, budget.amount)} budget.",
                suggestion="Ask the Stay agent for a cheaper option, or widen the budget.",
            )
        )
    if result.reserve_applied:
        conflicts.append(
            Conflict(
                rule=Rule.FEE_BASIS_UNKNOWN,
                item_id=stay.stay_id,
                detail=(
                    f"The site does not show whether the {total} stay total includes fees, so a "
                    f"{_percent(fee_reserve_pct)} reserve was added ({effective})."
                ),
                suggestion="Check the final price with fees before booking.",
            )
        )
    return tuple(conflicts)


# --- the whole run, and who owns what --------------------------------------------------------------


def check_plan(state: TripState, fee_reserve_pct: float) -> tuple[Conflict, ...]:
    """Every rule, in a fixed order: hours, slot windows, overlaps, travel times, missing legs, budget."""
    runs: tuple[Callable[[], tuple[Conflict, ...]], ...] = (
        lambda: check_hours(state),
        lambda: check_slot_windows(state),
        lambda: check_overlaps(state),
        lambda: check_travel_times(state),
        lambda: check_missing_legs(state),
        lambda: check_stay_budget(state, fee_reserve_pct),
    )
    return tuple(conflict for run in runs for conflict in run())


def owner_of(conflict: Conflict, state: TripState) -> Seat:
    """The seat that must revise: the item's agent, the Concierge for an anchor, the Stay agent for the stay and the budget.

    A `travel_time` conflict is filed against the later event, so its owner is that event's owner. An id nobody
    owns goes to the Concierge, which merges the plan and can decide.
    """
    if conflict.rule in (Rule.OVER_BUDGET, Rule.FEE_BASIS_UNKNOWN) or conflict.item_id == STAY_ID:
        return Seat.STAY
    if state.stay is not None and conflict.item_id == state.stay.stay_id:
        return Seat.STAY
    for entry in state.entries:
        if entry.item.item_id == conflict.item_id:
            return entry.agent
    if any(anchor.id == conflict.item_id for anchor in state.anchors):
        return Seat.CONCIERGE
    logger.warning("conflict %s names %s, which the plan does not contain; sending it to the concierge", conflict.rule, conflict.item_id)
    return Seat.CONCIERGE
