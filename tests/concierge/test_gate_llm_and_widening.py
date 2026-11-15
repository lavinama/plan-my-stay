"""The parts of the gate module beyond the basic budget check: the cheaper ceiling, widening, close calls and LLM arbitration."""

import re

import pytest

from plan_my_stay.concierge.gate import (
    ARBITRATE_SYSTEM,
    GateStatus,
    arbitrate,
    average_commute,
    cheaper_ceiling,
    gate_stay,
    widen_budget,
)
from plan_my_stay.concierge.llm import FakeLLM, LLMError
from plan_my_stay.enums import Mode
from plan_my_stay.messages.payloads import StayShortlistPayload
from plan_my_stay.models import Budget, CostBasis, StayOption

BUDGET = Budget(currency="USD", amount=1000)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def stay(rank: int, price: float, basis: str = CostBasis.TOTAL_INCL_FEES, **extra) -> StayOption:
    return StayOption(
        rank=rank,
        deciding_factor=extra.pop("deciding_factor", "commute"),
        stay_id=f"stay-{rank}",
        zone_id="z1",
        name=extra.pop("name", f"Stay {rank}"),
        total_price=price,
        cost_basis=basis,
        **extra,
    )


def commute(*minutes: float) -> dict:
    return {f"A{i}": {Mode.WALKING: m, Mode.TRANSIT: m + 5, Mode.DRIVING: None} for i, m in enumerate(minutes)}


# --- the gate's extra detail -------------------------------------------------------------------


def test_the_gate_reports_which_stays_it_skipped_and_which_fit():
    over, fits, also = stay(1, 900, CostBasis.CARD_TOTAL_FEES_UNKNOWN), stay(2, 950), stay(3, 400)
    result = gate_stay(StayShortlistPayload(stays=(over, fits, also)), BUDGET, 0.15)

    assert result.status is GateStatus.FITS and result.stay == fits
    assert result.skipped_stays == (over,) and result.fitting == (fits, also)


def test_when_nothing_fits_the_cheapest_total_after_the_reserve_is_a_hint():
    result = gate_stay(
        StayShortlistPayload(stays=(stay(1, 1100), stay(2, 1020), stay(3, 900, CostBasis.CARD_TOTAL_FEES_UNKNOWN))),
        BUDGET,
        0.15,
    )

    assert result.cheapest.rank == 2 and result.cheapest_effective_total == pytest.approx(1020)


# --- asking for cheaper stays and widening -----------------------------------------------------


def test_the_cheaper_ceiling_leaves_room_for_the_fee_reserve():
    ceiling = cheaper_ceiling(BUDGET, 0.15)

    assert ceiling.currency == "USD" and ceiling.amount == 869  # 1000 / 1.15, rounded down
    assert ceiling.amount * 1.15 <= 1000


def test_widening_the_budget_adds_a_quarter_rounded_up_to_ten():
    assert widen_budget(BUDGET).amount == 1250
    assert widen_budget(Budget(amount=333)).amount == 420  # 416.25 -> 420


# --- close calls -------------------------------------------------------------------------------


def test_average_commute_uses_the_fastest_mode_per_anchor():
    s = stay(1, 600, commute_minutes=commute(10, 20))

    assert average_commute(s, ["A0", "A1"]) == 15
    assert average_commute(s, ["A1"]) == 20
    assert average_commute(s, ["Z9"]) is None  # no data for that anchor
    assert average_commute(s, []) is None


# --- arbitration with an LLM -------------------------------------------------------------------

CHELSEA = stay(1, 640, name="Quiet Chelsea studio", commute_minutes=commute(17))
TRIBECA = stay(2, 655, name="TriBeCa loft", commute_minutes=commute(12))


def test_the_llm_chooses_and_explains_in_two_sentences():
    llm = FakeLLM(
        [
            '{"choice": "stay-2", "trade_off": "The TriBeCa loft costs $655.00 but cuts the commute to 12 min. The Chelsea studio is quieter."}'
        ]
    )

    result = arbitrate([CHELSEA, TRIBECA], anchor_ids=["A0"], context="Two stays are close.", llm=llm)

    assert result.used_llm and result.chosen == TRIBECA and result.runner_up == CHELSEA
    assert result.chosen_id == "stay-2" and result.trade_off == result.explanation
    assert (
        result.explanation
        == "The TriBeCa loft costs $655.00 but cuts the commute to 12 min. The Chelsea studio is quieter."
    )
    (call,) = llm.calls
    assert call.system == ARBITRATE_SYSTEM and call.json_schema is not None
    assert "TriBeCa loft" in call.prompt and "12 min" in call.prompt  # the facts the model may use


@pytest.mark.parametrize(
    "script",
    [
        [LLMError("model is down")],
        ["not json"],
        ['{"choice": "stay-9", "trade_off": "Nope."}'],  # not one of the options
        ['{"choice": "stay-2", "trade_off": ""}'],
        ['{"choice": "stay-2", "trade_off": "It saves $200.00 a night. Great."}'],  # an invented price
    ],
)
def test_a_failing_llm_or_an_invented_fact_falls_back_to_the_stay_agents_order(script):
    result = arbitrate([CHELSEA, TRIBECA], llm=FakeLLM(script))

    assert result.chosen == CHELSEA and not result.used_llm
    assert len(SENTENCE_END.split(result.explanation)) == 2


def test_the_explanation_is_cut_to_two_sentences():
    llm = FakeLLM(['{"choice": "stay-1", "trade_off": "One. Two. Three. Four."}'])

    assert arbitrate([CHELSEA, TRIBECA], llm=llm).explanation == "One. Two."


def test_one_stay_is_never_put_to_the_llm():
    llm = FakeLLM([])

    result = arbitrate([CHELSEA], llm=llm)

    assert result.chosen == CHELSEA and result.runner_up is None and llm.calls == ()
