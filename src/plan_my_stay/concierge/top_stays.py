"""The reply when the Concierge talks only to the Stay agent: the top three stays and the commute behind each.

Deterministic text, no model call. It says which meetings counted, and that the trips between the stay and the
meetings are not planned or checked yet (the Routing seat and the Verifier are not in this run).
"""

from collections.abc import Sequence

from plan_my_stay.budget import check_budget
from plan_my_stay.concierge.fmt import fmt_day, fmt_money
from plan_my_stay.fit import RankedStay
from plan_my_stay.models import CostBasis, Meeting
from plan_my_stay.state import TripState


def _zone_name(state: TripState, zone_id: str) -> str:
    return next((z.name for z in state.zones if z.id == zone_id), zone_id)


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def _stay_line(ranked: RankedStay, state: TripState, reserve: float) -> str:
    price = f"{fmt_money(ranked.total_price, ranked.currency)} total"
    if ranked.cost_basis in CostBasis.FEES_UNKNOWN:
        check = check_budget(ranked.total_price, ranked.cost_basis, budget=state.budget.amount, fee_reserve_pct=reserve)
        price += f" (fees not confirmed, about {fmt_money(check.effective_total, ranked.currency)} with a {reserve:.0%} reserve)"
    commute = (
        "average commute unknown"
        if ranked.average_commute_minutes is None
        else f"average commute {ranked.average_commute_minutes:.1f} min"
    )
    ending = " Recommended." if ranked.rank == 1 else ""
    return f"  {ranked.rank}. {ranked.name or ranked.stay_id} ({_zone_name(state, ranked.zone_id)}): {price}. {_upper_first(commute)}.{ending}"


def _labelled(meetings: Sequence[Meeting]) -> str:
    return ", ".join(f"{m.id} ({m.address})" for m in meetings)


def render_top_stays(
    state: TripState,
    meetings: Sequence[Meeting],
    *,
    notes: Sequence[str],
    warnings: Sequence[str],
    reserve: float,
) -> str:
    """The top stays in `state.ranked_stays`, best first, with the meetings that counted and any notes."""
    if not state.ranked_stays:
        raise ValueError("there is no stay to show")
    count = len(state.ranked_stays)
    nights = (state.check_out - state.check_in).days
    guests = f"{state.guests} guest" + ("s" if state.guests != 1 else "")
    heading = "Best stay" if count == 1 else f"Top {count} stays"
    counted = [m for m in meetings if m.counts_for_stay]
    skipped = [m for m in meetings if not m.counts_for_stay]
    lines = [
        f"{heading}: {state.city}, {fmt_day(state.check_in)} to {fmt_day(state.check_out)} ({nights} nights, {guests})",
        (
            f"Budget: {fmt_money(state.budget.amount, state.budget.currency)} for the stay plus transport. "
            "Only the stay price counts against it for now; transport cost is not calculated."
        ),
        "",
        f"Ranked by average commute to the meetings that count for the stay: {_labelled(counted)}. Shortest first.",
        *(_stay_line(r, state, reserve) for r in state.ranked_stays),
    ]
    if skipped:
        lines.append(f"Not counted: {_labelled(skipped)} starts before check-in, so it does not decide where you sleep.")
    lines.append("")
    if notes:
        lines += ["Notes", *(f"  - {note}" for note in notes), ""]
    if warnings:
        lines += ["Heads-up", *(f"  - {warning}" for warning in warnings), ""]
    lines.append(
        f"Prices are totals for {nights} nights. This is a stays-only answer: the trips between the stay and the "
        "meetings are not planned day by day or checked yet."
    )
    return "\n".join(lines) + "\n"
