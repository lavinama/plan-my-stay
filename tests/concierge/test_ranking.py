"""The Concierge ranks hotels from what each specialist returned (docs/SEAT_PROTOCOL.md, section 5).

Factors, each scored 0 to 1 as best / this (so a near tie stays a near tie, and 1 is the best hotel):
commute (average minutes to the meetings, +1 min), gym (nearest gym in metres, +100 m), restaurants (average rank of
the restaurants within the walk radius). Weights default to commute 0.5, gym 0.25, restaurants 0.25.
"""

import pytest

from plan_my_stay.concierge.ranking import Weights, rank_hotels
from plan_my_stay.fit import GymFitEntry, NearbyGym, RestaurantFitEntry
from plan_my_stay.models import CostBasis, StayOption

ANCHORS = ("B", "C")


def _stay(stay_id: str, commute: float | None, *, price: float = 500, rank: int = 1, zone: str = "z1") -> StayOption:
    return StayOption(rank=rank, deciding_factor="commute", stay_id=stay_id, zone_id=zone, name=stay_id.upper(),
                      total_price=price, average_commute_minutes=commute)


def _gym(stay_id: str, metres: float | None) -> GymFitEntry:
    gym = None if metres is None else NearbyGym(name=f"gym near {stay_id}", distance_m=metres)
    return GymFitEntry(stay_id=stay_id, nearest_gym=gym)


def _food(stay_id: str, average_rank: float | None, count: int = 3) -> RestaurantFitEntry:
    if average_rank is None:
        return RestaurantFitEntry(stay_id=stay_id, count=0)
    return RestaurantFitEntry(stay_id=stay_id, count=count, average_rank=average_rank)


# The demo's shape: the Stay agent's commute order is A < B < C, but C has a close gym and good restaurants and B has
# neither, so the combined order is A, C, B.
STAYS = (_stay("a", 11.5, rank=1), _stay("b", 12.5, rank=2), _stay("c", 13.0, rank=3))
GYMS = {"a": _gym("a", 150), "b": _gym("b", 1000), "c": _gym("c", 400)}
FOOD = {"a": _food("a", 2.0), "b": _food("b", 6.0), "c": _food("c", 2.0)}


def _rank(**kwargs):
    kwargs.setdefault("anchor_ids", ANCHORS)
    kwargs.setdefault("gym_fits", GYMS)
    kwargs.setdefault("restaurant_fits", FOOD)
    return rank_hotels(kwargs.pop("stays", STAYS), **kwargs)


def test_the_combined_order_can_differ_from_the_stay_agents_commute_order():
    ranked = _rank()

    assert [r.stay_id for r in ranked] == ["a", "c", "b"]
    assert [r.rank for r in ranked] == [1, 2, 3]


def test_scores_are_the_weighted_best_over_this_ratios():
    by_id = {r.stay_id: r for r in _rank()}

    assert by_id["a"].score == pytest.approx(1.0)
    # c: commute (11.5+1)/(13+1), gym (150+100)/(400+100), restaurants 2/2
    assert by_id["c"].score == pytest.approx(0.5 * 12.5 / 14 + 0.25 * 250 / 500 + 0.25 * 1.0)
    assert by_id["b"].score == pytest.approx(0.5 * 12.5 / 13.5 + 0.25 * 250 / 1100 + 0.25 * 2 / 6)
    assert by_id["b"].scores == pytest.approx({"commute": 12.5 / 13.5, "gym": 250 / 1100, "restaurants": 2 / 6})
    assert all(0 <= r.score <= 1 for r in by_id.values())


def test_each_ranked_hotel_carries_the_numbers_it_was_ranked_on():
    first = _rank()[0]

    assert (first.average_commute_minutes, first.nearest_gym_m, first.nearest_gym_name) == (11.5, 150, "gym near a")
    assert (first.restaurant_count, first.restaurant_average_rank) == (3, 2.0)
    assert (first.name, first.total_price, first.currency, first.zone_id) == ("A", 500, "USD", "z1")


def test_the_deciding_factor_is_what_put_it_ahead_of_the_next_hotel():
    by_id = {r.stay_id: r for r in _rank()}

    assert by_id["a"].deciding_factor == "gym"  # ahead of c mostly on the nearer gym
    assert by_id["c"].deciding_factor == "restaurants"  # ahead of b mostly on the restaurants
    assert by_id["b"].deciding_factor == "commute"  # last: its strongest weighted factor


def test_only_the_top_three_are_returned_and_fewer_hotels_return_fewer():
    five = tuple(_stay(f"s{i}", 10 + i) for i in range(5))

    assert [r.rank for r in _rank(stays=five, gym_fits=None, restaurant_fits=None)] == [1, 2, 3]
    assert len(_rank(stays=STAYS[:2])) == 2
    assert len(_rank(stays=five, top_n=4, gym_fits=None, restaurant_fits=None)) == 4
    assert _rank(stays=()) == ()


def test_a_hotel_with_no_gym_or_no_restaurants_nearby_is_ranked_down_not_dropped():
    gyms = {**GYMS, "a": _gym("a", None)}
    food = {**FOOD, "a": _food("a", None)}

    by_id = {r.stay_id: r for r in _rank(gym_fits=gyms, restaurant_fits=food)}

    assert by_id["a"].scores["gym"] == 0 and by_id["a"].scores["restaurants"] == 0
    assert (by_id["a"].nearest_gym_m, by_id["a"].restaurant_average_rank, by_id["a"].restaurant_count) == (None, None, 0)
    assert by_id["a"].rank > 1  # it lost its lead without those two


def test_a_hotel_the_agent_did_not_report_counts_as_having_no_data():
    gyms = {k: v for k, v in GYMS.items() if k != "c"}

    by_id = {r.stay_id: r for r in _rank(gym_fits=gyms)}

    assert by_id["c"].scores["gym"] == 0 and by_id["c"].nearest_gym_m is None


def test_a_factor_whose_agent_gave_nothing_is_left_out_and_the_weights_renormalise():
    ranked = _rank(gym_fits=None)  # no Gym agent in this run

    assert all("gym" not in r.scores for r in ranked)
    # commute 0.5 and restaurants 0.25 only: a scores 1.0, c scores (0.5*12.5/14 + 0.25) / 0.75
    by_id = {r.stay_id: r for r in ranked}
    assert by_id["a"].score == pytest.approx(1.0)
    assert by_id["c"].score == pytest.approx((0.5 * 12.5 / 14 + 0.25 * 1.0) / 0.75)


def test_with_no_specialist_data_the_order_is_the_stay_agents_commute_order():
    ranked = _rank(gym_fits=None, restaurant_fits=None)

    assert [r.stay_id for r in ranked] == ["a", "b", "c"]
    assert set(ranked[0].scores) == {"commute"}


def test_the_average_commute_is_computed_from_the_per_meeting_minutes_when_the_stay_agent_gave_none():
    commute = {"B": {"walking": 30, "driving": 9}, "C": {"walking": 20, "driving": 14}}  # fastest: 9 and 14 -> 11.5
    stay = StayOption(rank=1, deciding_factor="commute", stay_id="x", zone_id="z1", total_price=1, commute_minutes=commute)

    (ranked,) = rank_hotels([stay], anchor_ids=ANCHORS, gym_fits=None, restaurant_fits=None)

    assert ranked.average_commute_minutes == pytest.approx(11.5)


def test_a_hotel_with_no_commute_data_scores_zero_on_commute():
    stays = (_stay("a", 11.5), _stay("unknown", None))

    by_id = {r.stay_id: r for r in rank_hotels(stays, anchor_ids=ANCHORS, gym_fits=None, restaurant_fits=None)}

    assert by_id["unknown"].scores["commute"] == 0 and by_id["unknown"].rank == 2


def test_custom_weights_change_the_order():
    commute_only = Weights(commute=1, gym=0, restaurants=0)

    assert [r.stay_id for r in _rank(weights=commute_only)] == ["a", "b", "c"]
    gym_only = Weights(commute=0, gym=1, restaurants=0)
    assert [r.stay_id for r in _rank(weights=gym_only)][:2] == ["a", "c"]


@pytest.mark.parametrize("kwargs", [dict(commute=-1), dict(commute=0, gym=0, restaurants=0)])
def test_invalid_weights_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Weights(**kwargs)


def test_equal_scores_break_ties_on_commute_then_price_then_the_stay_agents_order():
    same = (_stay("x", 12, price=700, rank=1), _stay("y", 12, price=600, rank=2), _stay("z", 12, price=600, rank=3))

    ranked = rank_hotels(same, anchor_ids=ANCHORS, gym_fits=None, restaurant_fits=None)

    assert [r.stay_id for r in ranked] == ["y", "z", "x"]  # cheaper first; y before z by the Stay agent's order


def test_the_inputs_are_not_changed():
    stays, gyms, food = tuple(STAYS), dict(GYMS), dict(FOOD)

    _rank()

    assert (stays, gyms, food) == (STAYS, GYMS, FOOD)


def test_the_cost_basis_is_carried_so_the_itinerary_can_say_fees_are_unknown():
    stay = StayOption(rank=1, deciding_factor="commute", stay_id="x", zone_id="z1", total_price=640,
                      cost_basis=CostBasis.CARD_TOTAL_FEES_UNKNOWN, average_commute_minutes=10)

    (ranked,) = rank_hotels([stay], anchor_ids=ANCHORS, gym_fits=None, restaurant_fits=None)

    assert ranked.cost_basis == CostBasis.CARD_TOTAL_FEES_UNKNOWN
