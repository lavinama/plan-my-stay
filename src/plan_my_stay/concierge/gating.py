"""The budget-gate steps the Concierge takes around the Stay agent's shortlist (SEAT_PROTOCOL section 5).

`skipped_notes` explains a stay the budget set aside, and `settle_close_call` lets the model arbitrate when the top two
ranked hotels are a near tie. `ask_cheaper` and `widen_and_ask` are the two ways to answer "nothing fits": ask Stay for
cheaper options (twice), then widen the budget and the search area and say so on screen. Ranking itself is in
`ranking.py`; the Concierge ranks the hotels the budget leaves.
"""

from collections.abc import Sequence
from typing import Any

from plan_my_stay.concierge.fmt import fmt_money
from plan_my_stay.concierge.gate import (
    GateResult,
    arbitrate,
    cheaper_ceiling,
    check_stay,
    widen_budget,
)
from plan_my_stay.concierge.outbox import specialist_request
from plan_my_stay.concierge.run import MAX_ROUNDS, Phase, Result, Run, put, reply
from plan_my_stay.concierge.zones import widen_zones
from plan_my_stay.enums import Seat
from plan_my_stay.fit import RankedStay
from plan_my_stay.messages import Message
from plan_my_stay.models import StayOption
from plan_my_stay.ports import LLMPort
from plan_my_stay.state import TripState

CLOSE_SCORE_GAP = 0.05  # the top two hotels within this score are a near tie: the model arbitrates (when there is one)


def skipped_notes(state: TripState, gate: GateResult, *, reserve: float) -> tuple[str, ...]:
    """Say why the Stay agent's first pick is not in the ranking: it is over the budget once the fee reserve is added."""
    notes = []
    for skipped in gate.skipped_stays[:1]:
        check = check_stay(skipped, state.budget, reserve)
        total = fmt_money(check.effective_total, skipped.currency) if check else "more"
        with_reserve = f" with a {reserve:.0%} fee reserve" if check and check.reserve_applied else ""
        name = skipped.name or skipped.stay_id
        notes.append(f"{name} ranked first but would cost {total}{with_reserve}, over your budget, so it was set aside.")
    return tuple(notes)


def settle_close_call(
    ranked: tuple[RankedStay, ...], fitting: Sequence[StayOption], *, anchor_ids: Sequence[str], llm: LLMPort | None
) -> tuple[tuple[RankedStay, ...], tuple[str, ...]]:
    """When the top two hotels are a near tie and a model is available, let it pick the first and explain the
    trade-off in two sentences. Without a model, or when it fails, the ranking stands."""
    if llm is None or len(ranked) < 2 or ranked[0].score - ranked[1].score > CLOSE_SCORE_GAP:
        return ranked, ()
    if ranked[0].average_commute_minutes is None or ranked[1].average_commute_minutes is None:
        return ranked, ()  # no commute data to weigh: the model could only guess, so the tie-break (price) stands
    by_id = {s.stay_id: s for s in fitting}
    arbitration = arbitrate([by_id[ranked[0].stay_id], by_id[ranked[1].stay_id]], anchor_ids=anchor_ids, llm=llm)
    if not arbitration.used_llm:
        return ranked, ()
    if arbitration.chosen.stay_id == ranked[1].stay_id:
        ranked = (ranked[1].model_copy(update={"rank": 1}), ranked[0].model_copy(update={"rank": 2}), *ranked[2:])
    return ranked, (arbitration.explanation,)


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
        meetings=run.meetings,
    )
    return _waiting_for_stay(run, state, request, stay_round=round_), [request]


def widen_and_ask(run: Run) -> Result:
    """The rounds are used up: widen the budget, ask Stay again, and say so on screen and on the plan."""
    old = run.trip.budget
    state = run.trip.bump(budget=widen_budget(old))
    was, now = fmt_money(old.amount, old.currency), fmt_money(state.budget.amount, state.budget.currency)
    request = specialist_request(
        state,
        Seat.STAY,
        round=MAX_ROUNDS,
        budget=state.budget,
        reply_to=run.shortlist_id,
        note=" (widened budget)",
        meetings=run.meetings,
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
