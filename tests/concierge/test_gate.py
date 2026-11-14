"""The budget gate and the thin arbitration fallback."""

import re

import pytest

from plan_my_stay.concierge.gate import Arbitration, arbitrate, gate_stay
from plan_my_stay.messages.payloads import StayShortlistPayload
from plan_my_stay.models import Budget, CostBasis, StayOption

UNKNOWN = CostBasis.CARD_TOTAL_FEES_UNKNOWN
KNOWN = CostBasis.TOTAL_INCL_FEES
BUDGET = Budget(currency="USD", amount=1000)


def stay(rank: int, price: float, basis: str = UNKNOWN, *, currency: str = "USD", name: str | None = None) -> StayOption:
    return StayOption(
        rank=rank,
        deciding_factor="commute" if rank == 1 else "gym fit",
        stay_id=f"stay-{rank}",
        zone_id="z1",
        name=name or f"Stay {rank}",
        total_price=price,
        currency=currency,
        cost_basis=basis,
    )


def shortlist(*stays: StayOption) -> StayShortlistPayload:
    return StayShortlistPayload(stays=stays)


def test_the_top_stay_is_chosen_when_it_fits():
    result = gate_stay(shortlist(stay(1, 640), stay(2, 500)), BUDGET, 0.15)

    assert result.fits
    assert result.stay.rank == 1  # the Concierge checks the top stay; it does not re-rank by price
    assert result.skipped == 0
    assert result.check.effective_total == pytest.approx(640 * 1.15)


def test_a_total_with_unknown_fees_gets_the_reserve_and_can_go_over_budget():
    # 900 fits the budget on its face, but 900 + 15% = 1035 does not.
    result = gate_stay(shortlist(stay(1, 900)), BUDGET, 0.15)

    assert not result.fits
    assert result.stay is None


def test_a_known_fee_basis_gets_no_reserve():
    result = gate_stay(shortlist(stay(1, 1000, KNOWN)), BUDGET, 0.15)

    assert result.fits
    assert result.check.reserve_applied is False


def test_the_next_ranked_stay_that_fits_is_chosen_and_the_skip_is_counted():
    result = gate_stay(shortlist(stay(1, 900), stay(2, 850, KNOWN), stay(3, 500)), BUDGET, 0.15)

    assert result.stay.rank == 2
    assert result.skipped == 1


def test_nothing_fits_returns_the_cheapest_as_a_hint():
    result = gate_stay(shortlist(stay(1, 990), stay(2, 920), stay(3, 960)), BUDGET, 0.15)

    assert not result.fits
    assert result.cheapest.rank == 2
    assert result.check.effective_total == pytest.approx(920 * 1.15)
    assert result.check.within_budget is False


def test_an_empty_shortlist_means_nothing_fits_and_gives_no_hint():
    result = gate_stay(shortlist(), BUDGET, 0.15)

    assert not result.fits
    assert result.cheapest is None
    assert result.check is None


def test_the_cheapest_hint_compares_prices_with_their_reserve():
    # Rank 1 is cheaper on its face (950) but its fees are unknown; rank 2 is dearer (1010) with fees included.
    result = gate_stay(shortlist(stay(1, 950), stay(2, 1010, KNOWN)), BUDGET, 0.15)

    assert result.cheapest.rank == 2  # 1010 < 950 * 1.15 = 1092.5


def test_a_stay_priced_in_another_currency_is_never_chosen():
    result = gate_stay(shortlist(stay(1, 100, KNOWN, currency="EUR"), stay(2, 800, KNOWN)), BUDGET, 0.15)

    assert result.stay.rank == 2
    assert result.skipped == 1


def test_the_gate_leaves_its_input_alone():
    original = shortlist(stay(1, 900), stay(2, 500))
    gate_stay(original, BUDGET, 0.15)

    assert [s.rank for s in original.stays] == [1, 2]


def test_a_negative_reserve_is_rejected():
    with pytest.raises(ValueError):
        gate_stay(shortlist(stay(1, 100)), BUDGET, -0.1)


# --- arbitration: a thin fallback that keeps the Stay agent's order ---------------------------------------------------


def test_arbitrate_keeps_the_stay_agents_order():
    result = arbitrate([stay(1, 640), stay(2, 600)])

    assert isinstance(result, Arbitration)
    assert result.chosen.rank == 1
    assert result.runner_up.rank == 2
    assert result.used_llm is False


def test_the_explanation_is_two_sentences_and_names_the_trade_off():
    result = arbitrate([stay(1, 640, name="Quiet Chelsea studio"), stay(2, 600, name="TriBeCa loft")])

    assert len(re.findall(r"[.!?](?:\s|$)", result.explanation)) == 2
    assert "Quiet Chelsea studio" in result.explanation
    assert "commute" in result.explanation
    assert "TriBeCa loft" in result.explanation
    assert "$40.00" in result.explanation  # the price gap is the trade-off


def test_a_single_option_has_no_runner_up_but_still_two_sentences():
    result = arbitrate([stay(1, 640)])

    assert result.runner_up is None
    assert len(re.findall(r"[.!?](?:\s|$)", result.explanation)) == 2


def test_arbitrate_needs_at_least_one_option():
    with pytest.raises(ValueError):
        arbitrate([])


def test_arbitrate_never_calls_the_llm_in_this_first_pass():
    class Boom:
        def complete(self, **_: object) -> str:
            raise AssertionError("the LLM is deferred")

    assert arbitrate([stay(1, 640), stay(2, 600)], llm=Boom()).used_llm is False
