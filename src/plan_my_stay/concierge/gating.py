"""The budget-gate steps the Concierge takes around the Stay agent's shortlist (SEAT_PROTOCOL section 5, step 6).

`choose_stay` picks the stay to merge (arbitrating a close call and explaining a skipped one). `ask_cheaper` and
`widen_and_ask` are the two ways to answer "nothing fits": ask Stay for cheaper options (twice), then widen the
budget and the search area and say so on screen. The Concierge gates and arbitrates; it never ranks.
"""

from typing import Any

from plan_my_stay.concierge.fmt import fmt_money
from plan_my_stay.concierge.gate import (
    GateResult,
    arbitrate,
    cheaper_ceiling,
    check_stay,
    is_close_call,
    widen_budget,
)
from plan_my_stay.concierge.outbox import specialist_request
from plan_my_stay.concierge.run import MAX_ROUNDS, Phase, Result, Run, put, reply
from plan_my_stay.concierge.zones import widen_zones
from plan_my_stay.enums import Seat
from plan_my_stay.messages import Message
from plan_my_stay.models import StayOption
from plan_my_stay.ports import LLMPort
from plan_my_stay.state import TripState


def choose_stay(
    state: TripState, gate: GateResult, top: StayOption, *, reserve: float, llm: LLMPort | None
) -> tuple[StayOption, tuple[str, ...]]:
    """The gate's stay, unless the top two fitting stays are a close call: then arbitrate and explain."""
    chosen, notes = top, []
    counting = [a.id for a in state.anchors if a.counts_for_stay]
    if len(gate.fitting) >= 2 and is_close_call(gate.fitting[0], gate.fitting[1], counting):
        arbitration = arbitrate(gate.fitting[:2], anchor_ids=counting, llm=llm)
        chosen = arbitration.chosen
        notes.append(arbitration.explanation)
    for skipped in gate.skipped_stays[:1]:
        check = check_stay(skipped, state.budget, reserve)
        total = fmt_money(check.effective_total, skipped.currency) if check else "more"
        with_reserve = f" with a {reserve:.0%} fee reserve" if check and check.reserve_applied else ""
        name = skipped.name or skipped.stay_id
        notes.insert(
            0, f"{name} ranked first but would cost {total}{with_reserve}, over your budget, so it was set aside."
        )
    return chosen, tuple(notes)


def _waiting_for_stay(run: Run, state: TripState, request: Message, **changes: Any) -> Run:
    return run.with_(
        state=state,
        shortlist=None,
        shortlist_id=None,
        phase=Phase.COLLECTING,
        requests=put(run.requests, Seat.STAY, request),
        **changes,
    )


def ask_cheaper(run: Run, reserve: float) -> Result:
    """Ask Stay for options that fit even with the fee reserve added. The second ask also widens the search area."""
    round_ = run.stay_round + 1
    state = run.trip.bump(zones=widen_zones(run.trip.zones)) if round_ == MAX_ROUNDS else run.trip
    request = specialist_request(
        state,
        Seat.STAY,
        round=round_,
        budget=cheaper_ceiling(state.budget, reserve),
        reply_to=run.shortlist_id,
        note=f" (cheaper options, round {round_})",
    )
    return _waiting_for_stay(run, state, request, stay_round=round_), [request]


def widen_and_ask(run: Run) -> Result:
    """The rounds are used up: widen the budget, ask Stay again, and say so on screen and on the plan."""
    old = run.trip.budget
    state = run.trip.bump(budget=widen_budget(old))
    was, now = fmt_money(old.amount, old.currency), fmt_money(state.budget.amount, state.budget.currency)
    request = specialist_request(
        state, Seat.STAY, round=MAX_ROUNDS, budget=state.budget, reply_to=run.shortlist_id, note=" (widened budget)"
    )
    warning = (
        f"No stay fit your {was} budget, so the budget was widened to {now}. Check the stay price before you book."
    )
    run = _waiting_for_stay(run, state, request, widened=True)
    run = run.with_(warnings=(*run.warnings, warning))
    notice = (
        f"No stay fit your {was} budget after two tries (a fee reserve is added when fees are not confirmed), so I am "
        f"widening the search: budget up to {now} and a bigger search area. I will flag this on the plan."
    )
    return run, [reply(run, notice), request]
