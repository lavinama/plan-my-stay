"""The budget gate and arbitration helpers.

The Stay agent returns the hotels ranked by average commute; the Concierge ranks the ones the budget leaves from what
the Stay, Gym and Restaurant agents each reported (`ranking.py`). `gate_stay` finds the hotels that fit the budget once
the fee reserve is added; it never re-orders the list. `arbitrate` settles a near tie between two hotels and explains
the trade-off in two sentences, and falls back to the order it was given when the LLM fails.
"""

import json
import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from plan_my_stay.budget import BudgetCheck, check_budget
from plan_my_stay.concierge.fmt import fmt_money
from plan_my_stay.messages.payloads import StayShortlistPayload
from plan_my_stay.models import Budget, StayOption
from plan_my_stay.ports import LLMPort

logger = logging.getLogger(__name__)

WIDEN_PCT = 0.25  # the last resort when nothing fits: the budget grows by a quarter

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class GateStatus(StrEnum):
    FITS = "fits"
    NOTHING_FITS = "nothing_fits"


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    stay: StayOption | None  # the first stay, in the Stay agent's order, that fits with the fee reserve
    check: BudgetCheck | None  # the budget check of `stay`; when nothing fits, of the cheapest stay (the hint)
    skipped_stays: tuple[StayOption, ...]  # ranked above `stay` but over budget with the reserve
    fitting: tuple[StayOption, ...]  # every stay that fits, still in the Stay agent's order
    cheapest: StayOption | None  # a hint when nothing fits: the cheapest after the reserve

    @property
    def fits(self) -> bool:
        return self.status is GateStatus.FITS

    @property
    def skipped(self) -> int:
        return len(self.skipped_stays)

    @property
    def cheapest_effective_total(self) -> float | None:
        return self.check.effective_total if self.cheapest is not None and self.check is not None else None


def check_stay(stay: StayOption, budget: Budget, fee_reserve_pct: float) -> BudgetCheck | None:
    """None when the stay is priced in another currency: there is no conversion, so it cannot fit."""
    if stay.currency != budget.currency:
        return None
    return check_budget(stay.total_price, stay.cost_basis, budget=budget.amount, fee_reserve_pct=fee_reserve_pct)


def gate_stay(shortlist: StayShortlistPayload, budget: Budget, fee_reserve_pct: float) -> GateResult:
    """Check the shortlist against the budget, in the Stay agent's order. It never re-ranks."""
    checks = [(stay, check_stay(stay, budget, fee_reserve_pct)) for stay in shortlist.stays]
    fitting = tuple(stay for stay, check in checks if check is not None and check.within_budget)
    if fitting:
        index = next(i for i, (stay, check) in enumerate(checks) if check is not None and check.within_budget)
        chosen, chosen_check = checks[index]
        return GateResult(
            GateStatus.FITS, chosen, chosen_check, tuple(stay for stay, _ in checks[:index]), fitting, None
        )

    priced = [(stay, check) for stay, check in checks if check is not None]
    if not priced:
        return GateResult(GateStatus.NOTHING_FITS, None, None, (), (), None)
    cheapest, cheapest_check = min(priced, key=lambda pair: pair[1].effective_total)
    return GateResult(GateStatus.NOTHING_FITS, None, cheapest_check, (), (), cheapest)


def cheaper_ceiling(budget: Budget, fee_reserve_pct: float) -> Budget:
    """The raw-price ceiling to give the Stay agent when asking for cheaper stays: any stay at or under it fits
    the budget even if its fee basis is unknown and the reserve is added."""
    return budget.model_copy(update={"amount": max(1, math.floor(budget.amount / (1 + fee_reserve_pct)))})


def widen_budget(budget: Budget, pct: float = WIDEN_PCT) -> Budget:
    """The budget grown by `pct`, rounded up to the next 10 (the Concierge says so on screen)."""
    return budget.model_copy(update={"amount": math.ceil(round(budget.amount * (1 + pct) / 10, 6)) * 10})


def average_commute(stay: StayOption, anchor_ids: Sequence[str]) -> float | None:
    """Mean of the fastest-mode minutes to each anchor. None when any of the anchors has no data."""
    fastest: list[float] = []
    for anchor_id in anchor_ids:
        known = [m for m in stay.commute_minutes.get(anchor_id, {}).values() if m is not None]
        if not known:
            return None
        fastest.append(min(known))
    return sum(fastest) / len(fastest) if fastest else None


@dataclass(frozen=True)
class Arbitration:
    """What the Concierge chose between close stays and the two-sentence trade-off it explains to the user."""

    chosen: StayOption
    runner_up: StayOption | None
    explanation: str  # two sentences
    used_llm: bool

    @property
    def chosen_id(self) -> str:
        return self.chosen.stay_id

    @property
    def trade_off(self) -> str:
        return self.explanation


ARBITRATE_SYSTEM = (
    "You help a business traveler choose between close stays for a trip. Choose one option and explain the "
    "trade-off in exactly two sentences. Use only the facts given: never add a price, a time or a place."
)


def _name(stay: StayOption) -> str:
    return stay.name or stay.stay_id


def _money(stay: StayOption) -> str:
    return fmt_money(stay.total_price, stay.currency)


def arbitrate(
    stays: Sequence[StayOption],
    *,
    anchor_ids: Sequence[str] = (),
    context: str = "These stays are close on price and commute.",
    llm: LLMPort | None = None,
) -> Arbitration:
    """Choose between close stays. They come in the Stay agent's order, which is the fallback choice.

    With an LLM it picks one and explains the trade-off; any failure, or an explanation that adds a number,
    falls back to the Stay agent's order with a template explanation. One stay is never put to the LLM."""
    if not stays:
        raise ValueError("arbitrate needs at least one stay")
    if len(stays) == 1 or llm is None:
        return _fallback(stays)
    try:
        return _ask_llm(stays, anchor_ids, context, llm)
    except Exception as exc:  # noqa: BLE001 - any LLM failure falls back to the Stay agent's order
        logger.warning("arbitration fell back to the Stay agent's order (%s)", type(exc).__name__)
        return _fallback(stays)


def _fallback(stays: Sequence[StayOption]) -> Arbitration:
    first = stays[0]
    runner_up = stays[1] if len(stays) > 1 else None
    opening = f"{_name(first)} is the Stay agent's top pick on {first.deciding_factor}, at {_money(first)}."
    if runner_up is None:
        return Arbitration(first, None, f"{opening} It is the only stay that fits.", used_llm=False)
    gap = abs(first.total_price - runner_up.total_price)
    if first.currency != runner_up.currency:
        price = "is priced in another currency"
    elif gap == 0:
        price = "costs the same"
    else:
        price = f"costs {fmt_money(gap, runner_up.currency)} {'less' if runner_up.total_price < first.total_price else 'more'}"
    return Arbitration(
        first,
        runner_up,
        f"{opening} {_name(runner_up)} {price} but ranks lower on {runner_up.deciding_factor}.",
        used_llm=False,
    )


def _facts(stay: StayOption, anchor_ids: Sequence[str]) -> str:
    facts = f"total {_money(stay)}; decided on {stay.deciding_factor}"
    commute = stay.average_commute_minutes  # what the Stay agent reported; else work it out from the per-meeting times
    if commute is None and anchor_ids:
        commute = average_commute(stay, anchor_ids)
    return facts + (f"; commute {commute:.0f} min on average" if commute is not None else "")


def _ask_llm(stays: Sequence[StayOption], anchor_ids: Sequence[str], context: str, llm: LLMPort) -> Arbitration:
    ids = [stay.stay_id for stay in stays]
    schema = {
        "type": "object",
        "properties": {"choice": {"type": "string", "enum": ids}, "trade_off": {"type": "string"}},
        "required": ["choice", "trade_off"],
    }
    listing = "\n".join(f"- {stay.stay_id}: {_name(stay)}; {_facts(stay, anchor_ids)}" for stay in stays)
    answer = json.loads(
        llm.complete(system=ARBITRATE_SYSTEM, prompt=f"{context}\n\nOptions:\n{listing}", json_schema=schema)
    )
    chosen = next((stay for stay in stays if stay.stay_id == answer["choice"]), None)
    if chosen is None:
        raise ValueError("the LLM chose an unknown option")
    explanation = " ".join(_SENTENCE_BREAK.split(str(answer["trade_off"]).strip())[:2]).strip()
    if not explanation:
        raise ValueError("the LLM gave no trade-off")
    if not _numbers(explanation) <= _numbers(f"{context} {listing}"):
        raise ValueError("the LLM's trade-off added a number")
    runner_up = next((stay for stay in stays if stay is not chosen), None)
    return Arbitration(chosen, runner_up, explanation, used_llm=True)


def _numbers(text: str) -> set[float]:
    return {float(match.replace(",", "")) for match in _NUMBER.findall(text)}
