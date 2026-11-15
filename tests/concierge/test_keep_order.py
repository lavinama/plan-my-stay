"""With only the Stay agent, the Concierge keeps its order (docs/PLAN.md, "How hotels are ranked"): it drops what the
budget excludes upstream and shows the first three. The score is only the numbers behind the placement."""

import pytest

from plan_my_stay.concierge.ranking import keep_stay_order
from plan_my_stay.models import CostBasis, StayOption


def _stay(stay_id: str, commute: float | None, rank: int, *, price: float = 500, **extra) -> StayOption:
    return StayOption(rank=rank, deciding_factor="commute", stay_id=stay_id, zone_id="z1", name=stay_id.upper(),
                      total_price=price, average_commute_minutes=commute, **extra)


STAYS = (_stay("a", 11.5, 1), _stay("b", 12.5, 2), _stay("c", 13.0, 3), _stay("d", 20.0, 4))


def test_the_stay_agents_order_is_kept_and_only_the_first_three_are_shown():
    shown = keep_stay_order(STAYS, anchor_ids=("B", "C"))

    assert [r.stay_id for r in shown] == ["a", "b", "c"]
    assert [r.rank for r in shown] == [1, 2, 3]


def test_the_order_is_kept_even_when_it_is_not_the_order_of_the_commute_numbers():
    odd = (_stay("slow", 30.0, 1), _stay("fast", 10.0, 2))  # the Stay agent knows something the numbers do not show

    assert [r.stay_id for r in keep_stay_order(odd, anchor_ids=("B",))] == ["slow", "fast"]


def test_each_placement_carries_its_commute_price_and_a_score_against_the_best():
    first, second, *_ = keep_stay_order(STAYS, anchor_ids=("B", "C"))

    assert (first.average_commute_minutes, first.total_price, first.name, first.zone_id) == (11.5, 500, "A", "z1")
    assert first.scores == {"commute": pytest.approx(1.0)}
    assert second.scores["commute"] == pytest.approx(12.5 / 13.5)  # (best + 1) / (this + 1) minutes
    assert first.deciding_factor == "commute" and second.score == pytest.approx(12.5 / 13.5)


def test_no_gym_or_restaurant_numbers_are_claimed():
    (only,) = keep_stay_order(STAYS[:1], anchor_ids=("B",))

    assert (only.nearest_gym_m, only.nearest_gym_name, only.restaurant_count, only.restaurant_average_rank) == (None,) * 4


def test_fewer_than_three_hotels_show_fewer_and_none_shows_nothing():
    assert len(keep_stay_order(STAYS[:2], anchor_ids=("B",))) == 2
    assert keep_stay_order((), anchor_ids=("B",)) == ()


def test_the_commute_is_worked_out_from_the_per_meeting_times_when_the_average_is_missing():
    commute = {"B": {"walking": 30, "driving": 9}, "C": {"walking": 20, "driving": 14}}  # fastest 9 and 14: 11.5
    stay = StayOption(rank=1, deciding_factor="commute", stay_id="x", zone_id="z1", total_price=1, commute_minutes=commute)

    (shown,) = keep_stay_order([stay], anchor_ids=("B", "C"))

    assert shown.average_commute_minutes == pytest.approx(11.5)


def test_a_hotel_with_no_commute_data_is_still_shown_with_no_score():
    shown = keep_stay_order((_stay("a", 11.5, 1), _stay("blank", None, 2)), anchor_ids=("B",))

    assert shown[1].stay_id == "blank" and shown[1].average_commute_minutes is None
    assert shown[1].scores == {"commute": 0.0}


def test_when_nobody_has_commute_data_there_are_no_scores_at_all():
    shown = keep_stay_order((_stay("a", None, 1), _stay("b", None, 2)), anchor_ids=("B",))

    assert [r.scores for r in shown] == [{}, {}] and [r.stay_id for r in shown] == ["a", "b"]


def test_the_cost_basis_is_carried_so_the_reply_can_say_fees_are_unknown():
    stay = _stay("x", 10, 1, price=640, cost_basis=CostBasis.CARD_TOTAL_FEES_UNKNOWN)

    assert keep_stay_order([stay], anchor_ids=("B",))[0].cost_basis == CostBasis.CARD_TOTAL_FEES_UNKNOWN


def test_the_inputs_are_not_changed():
    before = tuple(STAYS)

    keep_stay_order(STAYS, anchor_ids=("B", "C"))

    assert STAYS == before
