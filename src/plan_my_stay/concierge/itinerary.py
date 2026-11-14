"""The itinerary: what the Concierge sends the person.

`render_template` is a deterministic template that always works: day by day, meetings with their addresses,
workouts, meals, the stay with its total and fee-basis note, transport mode and minutes per leg, and every
warning (open conflicts count as warnings). `render_itinerary` adds an optional frontier-model polish of
the same facts. The polish may reword but not add facts: every price, clock time and travel time in it must
also be in the template, and every warning and meeting address must survive verbatim; otherwise the
template is sent. Meeting addresses are the user's own and only ever go to the user and to this one LLM call.
"""

import datetime as dt
import logging
import re
from collections.abc import Mapping, Sequence

from plan_my_stay.concierge.fmt import fmt_clock, fmt_day, fmt_minutes, fmt_money
from plan_my_stay.concierge.timeline import Event, build_events
from plan_my_stay.messages.payloads import VerdictPayload
from plan_my_stay.models import Address, Conflict, CostBasis, Leg, ProposalItem
from plan_my_stay.ports import LLMPort
from plan_my_stay.state import TripState

logger = logging.getLogger(__name__)

_RULE_TITLES: Mapping[str, str] = {
    "open_during_slot": "Closed at the planned time",
    "unknown_hours": "Opening hours unknown",
    "outside_slot_window": "Outside its time window",
    "overlaps_event": "Overlaps another event",
    "travel_time": "Not enough time to travel",
    "missing_leg": "Travel time not checked",
    "over_budget": "Over budget",
    "fee_basis_unknown": "Fees not confirmed",
}

_BASIS_NOTES: Mapping[str, str] = {
    CostBasis.CARD_TOTAL_FEES_UNKNOWN: "card total, fees and taxes not confirmed",
    CostBasis.UNKNOWN: "price basis unknown, fees and taxes not confirmed",
    CostBasis.TOTAL_INCL_FEES: "total including fees and taxes",
}

_PRICE = re.compile(r"(?:[$€£]\s?|\b(?:USD|EUR|GBP|CAD|AUD|CHF|JPY|CNY|INR|MXN)\s)(\d[\d,]*(?:\.\d+)?)")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_AM_PM = re.compile(r"(?<![\d:])(\d{1,2})\s?([ap])\.?m\b", re.IGNORECASE)
_MINUTES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:minutes?|min)\b")

POLISH_SYSTEM = (
    "You polish a trip itinerary so it reads well for a busy traveler. Keep every fact exactly as given: do not add, "
    "change or drop any price, clock time, travel time, address, name or warning. Keep clock times in 24-hour form. "
    "Copy every line under 'Heads-up' word for word. Reply with the itinerary text only."
)


# --- facts a polish may not add ----------------------------------------------------------------


def extract_prices(text: str) -> set[float]:
    return {float(match.replace(",", "")) for match in _PRICE.findall(text)}


def extract_times(text: str) -> set[str]:
    """Clock times as `HH:MM`. A 12-hour `9 pm` is kept as `9pm`, which never equals a template time."""
    times = {f"{int(hour):02d}:{minute}" for hour, minute in _CLOCK.findall(text)}
    return times | {f"{hour}{half.lower()}m" for hour, half in _AM_PM.findall(text)}


def extract_minutes(text: str) -> set[float]:
    return {float(match) for match in _MINUTES.findall(text)}


# --- the template ------------------------------------------------------------------------------


def describe_conflict(conflict: Conflict) -> str:
    """A conflict in plain words for the user: the rule's title, the detail and the suggestion."""
    title = _RULE_TITLES.get(conflict.rule, "Needs a look")
    suggestion = f" Suggestion: {conflict.suggestion}" if conflict.suggestion else ""
    return f"{title}: {conflict.detail}{suggestion}"


def collect_warnings(state: TripState, verdict: VerdictPayload | None, warnings: Sequence[str]) -> list[str]:
    """Every warning once, in order: the caller's, the open conflicts, then the verdict's conflicts."""
    lines = [*warnings, *(describe_conflict(c) for c in state.open_conflicts)]
    if verdict is not None:
        lines.extend(describe_conflict(c) for c in verdict.conflicts)
    return list(dict.fromkeys(line.strip() for line in lines if line.strip()))


def _address_line(address: Address | None) -> str:
    if address is None:
        return ""
    city_zip = " ".join(part for part in (address.city, address.zip) if part)
    return ", ".join(part for part in (address.street, city_zip) if part)


def _price_note(item: ProposalItem) -> str:
    if item.cost.amount is None:
        return ""
    basis = ", day pass" if item.cost.basis == CostBasis.DAY_PASS else ""
    return f" ({fmt_money(item.cost.amount, item.cost.currency)}{basis})"


def labels_for(state: TripState) -> dict[str, str]:
    """How the itinerary names each place: "Meeting A", an item's name, "the stay"."""
    labels = {"stay": "the stay"}
    labels.update({a.id: f"Meeting {a.id}" for a in state.anchors})
    labels.update({e.item.item_id: e.item.name for e in state.entries})
    return labels


def _event_line(event: Event, state: TripState, meeting_addresses: Mapping[str, str]) -> str:
    when = f"{fmt_clock(event.start)}-{fmt_clock(event.end)}"
    if event.anchor is not None:
        address = meeting_addresses.get(event.anchor.id)
        return f"  {when}  Meeting {event.anchor.id}" + (f", {address}" if address else "")
    if event.entry is None:  # an event is an anchor or an entry; this keeps the type checker honest
        return f"  {when}  {event.id}"
    item = event.entry.item
    slot = next((s for s in state.slots if s.id == item.slot_id), None)
    kind = slot.kind.replace("_", " ").capitalize() if slot is not None else "Stop"
    address = _address_line(item.address)
    return f"  {when}  {kind}: {item.name}" + (f", {address}" if address else "") + _price_note(item)


def _leg_line(leg: Leg, labels: Mapping[str, str]) -> str:
    start = labels.get(leg.from_id, leg.from_id)
    end = labels.get(leg.to_id, leg.to_id)
    minutes = leg.minutes_by_mode.get(leg.chosen_mode) if leg.chosen_mode is not None else None
    if leg.chosen_mode is None or minutes is None:
        outcome = "no route found"
    else:
        outcome = f"{leg.chosen_mode.value} {fmt_minutes(minutes)}"
    return f"  {fmt_clock(leg.depart_at)}  Travel from {start} to {end}: {outcome}"


def _stay_lines(state: TripState) -> list[str]:
    stay = state.stay
    if stay is None:
        return ["  No stay chosen yet."]
    nights = (state.check_out - state.check_in).days
    zone = next((z.name for z in state.zones if z.id == stay.zone_id), stay.zone_id)
    basis = _BASIS_NOTES.get(stay.cost_basis, f"price basis: {stay.cost_basis}")
    return [
        f"  {stay.name or stay.stay_id} ({zone}): {fmt_money(stay.total_price, stay.currency)} total for {nights} nights",
        f"  Price basis: {basis}.",
        f"  What decided it: {stay.deciding_factor}.",
    ]


def _day_lines(state: TripState, meeting_addresses: Mapping[str, str]) -> list[str]:
    events = build_events(state)
    labels = labels_for(state)
    days = {state.check_in + dt.timedelta(days=n) for n in range((state.check_out - state.check_in).days + 1)}
    days |= {e.start.date() for e in events} | {leg.depart_at.date() for leg in state.legs}
    lines: list[str] = []
    for day in sorted(days):
        timed = [(e.start, 0, _event_line(e, state, meeting_addresses)) for e in events if e.start.date() == day]
        timed += [(leg.depart_at, 1, _leg_line(leg, labels)) for leg in state.legs if leg.depart_at.date() == day]
        lines.append(fmt_day(day))
        lines.extend(line for _, _, line in sorted(timed, key=lambda row: (row[0], row[1])))
        if not timed:
            lines.append("  Check out." if day == state.check_out else "  Nothing planned.")
        elif day == state.check_out:
            lines.append("  Check out.")
        lines.append("")
    return lines


def render_template(
    state: TripState,
    verdict: VerdictPayload | None,
    *,
    meeting_addresses: Mapping[str, str],
    warnings: Sequence[str],
    notes: Sequence[str] = (),
) -> str:
    """The deterministic itinerary. Never calls a model, so it always works."""
    nights = (state.check_out - state.check_in).days
    guests = f"{state.guests} guest" + ("s" if state.guests != 1 else "")
    lines = [
        f"Trip plan: {state.city}, {fmt_day(state.check_in)} to {fmt_day(state.check_out)} ({nights} nights, {guests})",
        f"Budget: {fmt_money(state.budget.amount, state.budget.currency)} for the stay plus transport. "
        "Only the stay price counts against it for now; transport cost is not calculated.",
        "",
        "Stay",
        *_stay_lines(state),
        "",
        "Day by day",
        *_day_lines(state, meeting_addresses),
    ]
    if notes:
        lines += ["Notes", *(f"  - {note}" for note in notes), ""]
    heads_up = collect_warnings(state, verdict, warnings)
    if heads_up:
        lines += ["Heads-up", *(f"  - {warning}" for warning in heads_up), ""]
    return "\n".join(lines).rstrip() + "\n"


# --- the polish --------------------------------------------------------------------------------


def _faithful(polished: str, template: str, required: Sequence[str]) -> bool:
    """True when the polish adds no price, time or travel time and keeps every required phrase verbatim."""
    return (
        bool(polished.strip())
        and extract_prices(polished) <= extract_prices(template)
        and extract_times(polished) <= extract_times(template)
        and extract_minutes(polished) <= extract_minutes(template)
        and all(phrase in polished for phrase in required)
    )


def render_itinerary(
    state: TripState,
    verdict: VerdictPayload | None,
    *,
    meeting_addresses: Mapping[str, str],
    warnings: Sequence[str],
    llm: LLMPort | None,
    notes: Sequence[str] = (),
) -> str:
    """The template, or a frontier-model polish of it when `llm` is given and the polish adds no facts."""
    template = render_template(state, verdict, meeting_addresses=meeting_addresses, warnings=warnings, notes=notes)
    if llm is None:
        return template
    required = [
        *collect_warnings(state, verdict, warnings),
        *(meeting_addresses[a.id] for a in state.anchors if a.id in meeting_addresses),
    ]
    try:
        polished = llm.complete(system=POLISH_SYSTEM, prompt=f"Itinerary:\n\n{template}")
    except Exception as exc:  # noqa: BLE001 - the template is always a safe answer
        logger.warning("itinerary polish failed (%s); sending the template", type(exc).__name__)
        return template
    if not _faithful(polished, template, required):
        logger.warning("itinerary polish added or dropped a fact; sending the template")
        return template
    return polished
