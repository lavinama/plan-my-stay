"""A near tie between the top two hotels is settled by the model (when there is one); otherwise the ranking stands."""

import json

from plan_my_stay.concierge.gating import CLOSE_SCORE_GAP, settle_close_call
from plan_my_stay.concierge.llm import FakeLLM
from plan_my_stay.concierge.ranking import rank_hotels
from plan_my_stay.models import StayOption

TRADE_OFF = "The loft costs $700.00 with a 12 min average commute. The studio costs $680.00 with 12 min."


def _stay(stay_id: str, commute: float | None, price: float, rank: int) -> StayOption:
    return StayOption(rank=rank, deciding_factor="commute", stay_id=stay_id, zone_id="z1", name=stay_id,
                      total_price=price, average_commute_minutes=commute)


def _ranked(*stays: StayOption):
    return rank_hotels(stays, anchor_ids=("B", "C"), gym_fits=None, restaurant_fits=None)


def _llm(choice: str) -> FakeLLM:
    return FakeLLM([json.dumps({"choice": choice, "trade_off": TRADE_OFF})])


# commute 12.0 and 12.4 minutes: scores 1.0 and 13/13.4 = 0.97, a gap of 0.03: inside the 0.05 near-tie band
LOFT, STUDIO = _stay("loft", 12.0, 700, 1), _stay("studio", 12.4, 680, 2)


def test_the_model_can_promote_the_runner_up_and_the_ranks_are_renumbered():
    ranked = _ranked(LOFT, STUDIO)
    assert [r.stay_id for r in ranked] == ["loft", "studio"]

    settled, notes = settle_close_call(ranked, [LOFT, STUDIO], anchor_ids=("B", "C"), llm=_llm("studio"))

    assert [(r.stay_id, r.rank) for r in settled] == [("studio", 1), ("loft", 2)]
    assert notes == (TRADE_OFF,)


def test_when_the_model_confirms_the_leader_the_order_stands_but_the_trade_off_is_kept():
    ranked = _ranked(LOFT, STUDIO)

    settled, notes = settle_close_call(ranked, [LOFT, STUDIO], anchor_ids=("B", "C"), llm=_llm("loft"))

    assert [r.stay_id for r in settled] == ["loft", "studio"] and notes == (TRADE_OFF,)


def test_a_third_hotel_keeps_its_place_when_the_top_two_swap():
    far = _stay("far", 30.0, 500, 3)
    ranked = _ranked(LOFT, STUDIO, far)

    settled, _ = settle_close_call(ranked, [LOFT, STUDIO, far], anchor_ids=("B", "C"), llm=_llm("studio"))

    assert [(r.stay_id, r.rank) for r in settled] == [("studio", 1), ("loft", 2), ("far", 3)]


def test_without_a_model_the_ranking_stands_and_nothing_is_added():
    ranked = _ranked(LOFT, STUDIO)

    assert settle_close_call(ranked, [LOFT, STUDIO], anchor_ids=("B", "C"), llm=None) == (ranked, ())


def test_a_clear_gap_is_not_arbitrated():
    llm = FakeLLM([])
    ranked = _ranked(LOFT, _stay("slow", 25.0, 680, 2))
    assert ranked[0].score - ranked[1].score > CLOSE_SCORE_GAP

    assert settle_close_call(ranked, [LOFT, STUDIO], anchor_ids=("B", "C"), llm=llm) == (ranked, ())
    assert llm.calls == ()


def test_hotels_with_no_commute_data_are_not_arbitrated():
    llm = FakeLLM([])
    a, b = _stay("a", None, 600, 1), _stay("b", None, 610, 2)
    ranked = _ranked(a, b)  # tied on every factor, so inside the band, but there is nothing to weigh

    assert settle_close_call(ranked, [a, b], anchor_ids=("B", "C"), llm=llm) == (ranked, ())
    assert llm.calls == ()


def test_a_single_hotel_needs_no_arbitration():
    ranked = _ranked(LOFT)
    assert settle_close_call(ranked, [LOFT], anchor_ids=("B", "C"), llm=FakeLLM([])) == (ranked, ())
