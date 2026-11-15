"""The messages that let the Concierge rank hotels: hotel_query out, gym_fit and restaurant_fit back.

The Stay agent returns hotels ranked by average commute; the Gym agent the nearest gym to each hotel; the
Restaurant agent the average rank of the restaurants within a 10-minute walk of each hotel. The Concierge
combines the three (docs/SEAT_PROTOCOL.md, section 5).
"""

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.fit import RankedStay
from plan_my_stay.messages import MessageParseError, build_message, parse_message, serialize
from plan_my_stay.models import StayOption

HOTEL = {"stay_id": "stay-chelsea-1", "zone_id": "z1", "name": "Quiet Chelsea studio", "lat": 40.7465, "lng": -74.0014}


def _send(type_, sender, to, payload):
    return build_message(type=type_, sender=sender, to=to, trip_id=demo.TRIP_ID, user_id=demo.USER_ID,
                         state_version=1, summary="hotel fit", payload=payload)


def _query(**overrides):
    payload = {"hotels": [HOTEL], **overrides}
    return _send(MessageType.HOTEL_QUERY, Seat.CONCIERGE, [Seat.GYM, Seat.RESTAURANT], payload)


def test_a_hotel_query_defaults_to_a_ten_minute_walk_and_round_trips():
    msg = _query()

    assert msg.payload.radius_minutes == 10
    assert msg.payload.hotels[0].location_precision.value == "exact"
    assert parse_message(serialize(msg)) == msg


def test_a_hotel_query_needs_coordinates_so_the_agents_can_measure_distance():
    with pytest.raises(MessageParseError):
        _query(hotels=[{"stay_id": "s", "zone_id": "z1"}])


def test_a_hotel_query_needs_at_least_one_hotel_and_a_positive_radius():
    with pytest.raises(MessageParseError):
        _query(hotels=[])
    with pytest.raises(MessageParseError):
        _query(radius_minutes=0)


def test_a_zone_level_hotel_is_marked_as_zone_precision():
    zone_level = {**HOTEL, "location_precision": "zone"}
    assert _query(hotels=[zone_level]).payload.hotels[0].location_precision.value == "zone"


def test_gym_fit_carries_the_nearest_gym_per_hotel_or_none_when_there_is_no_gym():
    near = {"stay_id": "stay-chelsea-1", "nearest_gym": {"name": "Example HIIT Studio", "distance_m": 150.5}}
    none = {"stay_id": "stay-tribeca-1", "nearest_gym": None}
    msg = _send(MessageType.GYM_FIT, Seat.GYM, [Seat.CONCIERGE], {"hotels": [near, none]})

    assert msg.payload.hotels[0].nearest_gym.distance_m == 150.5
    assert msg.payload.hotels[1].nearest_gym is None
    assert parse_message(serialize(msg)) == msg


def test_gym_fit_rejects_a_negative_distance():
    bad = {"stay_id": "s", "nearest_gym": {"name": "x", "distance_m": -1}}
    with pytest.raises(MessageParseError):
        _send(MessageType.GYM_FIT, Seat.GYM, [Seat.CONCIERGE], {"hotels": [bad]})


def _restaurant_fit(entry):
    return _send(MessageType.RESTAURANT_FIT, Seat.RESTAURANT, [Seat.CONCIERGE], {"hotels": [entry]})


def test_restaurant_fit_carries_the_average_rank_of_restaurants_in_the_radius():
    entry = {"stay_id": "stay-chelsea-1", "count": 2, "average_rank": 2.5,
             "top": [{"name": "Urban Vegan Kitchen", "rank": 1, "walk_minutes": 6}, {"name": "Cafe B", "rank": 4}]}
    msg = _restaurant_fit(entry)

    assert (msg.payload.hotels[0].count, msg.payload.hotels[0].average_rank) == (2, 2.5)
    assert msg.payload.radius_minutes == 10
    assert parse_message(serialize(msg)) == msg


def test_restaurant_fit_with_nothing_in_the_radius_has_no_average():
    assert _restaurant_fit({"stay_id": "s", "count": 0}).payload.hotels[0].average_rank is None
    with pytest.raises(MessageParseError):
        _restaurant_fit({"stay_id": "s", "count": 0, "average_rank": 3.0})


def test_restaurant_fit_with_restaurants_needs_an_average_of_at_least_rank_one():
    with pytest.raises(MessageParseError):
        _restaurant_fit({"stay_id": "s", "count": 3})
    with pytest.raises(MessageParseError):
        _restaurant_fit({"stay_id": "s", "count": 3, "average_rank": 0.5})


@pytest.mark.parametrize(
    "type_, sender, to, payload",
    [
        (MessageType.HOTEL_QUERY, Seat.GYM, [Seat.CONCIERGE], {"hotels": [HOTEL]}),
        (MessageType.GYM_FIT, Seat.RESTAURANT, [Seat.CONCIERGE], {"hotels": []}),
        (MessageType.GYM_FIT, Seat.CONCIERGE, [Seat.GYM], {"hotels": []}),
        (MessageType.RESTAURANT_FIT, Seat.GYM, [Seat.CONCIERGE], {"hotels": []}),
    ],
)
def test_only_the_right_seat_may_send_each_type(type_, sender, to, payload):
    with pytest.raises(MessageParseError) as err:
        _send(type_, sender, to, payload)
    assert err.value.code.value == "unexpected_sender"


def test_a_stay_may_carry_its_average_commute_and_it_cannot_be_negative():
    stay = StayOption(rank=1, deciding_factor="commute", stay_id="s", zone_id="z1", total_price=500, average_commute_minutes=11.5)
    assert stay.average_commute_minutes == 11.5
    assert StayOption(rank=1, deciding_factor="x", stay_id="s", zone_id="z1", total_price=1).average_commute_minutes is None
    with pytest.raises(ValueError):
        StayOption(rank=1, deciding_factor="x", stay_id="s", zone_id="z1", total_price=1, average_commute_minutes=-1)


def _ranked(rank: int = 1) -> RankedStay:
    return RankedStay(rank=rank, stay_id="stay-chelsea-1", zone_id="z1", name="Quiet Chelsea studio", total_price=640,
                      score=0.91, average_commute_minutes=11.5, nearest_gym_m=150, nearest_gym_name="Example HIIT Studio",
                      restaurant_count=5, restaurant_average_rank=2.4, deciding_factor="commute",
                      scores={"commute": 1.0, "gym": 0.8, "restaurants": 0.9})


def test_the_ranking_is_kept_on_the_trip_state_but_is_not_part_of_the_plan_hash():
    planned = demo.demo_state_with_plan()
    ranked = planned.bump(ranked_stays=(_ranked(),))

    assert ranked.ranked_stays[0].stay_id == "stay-chelsea-1"
    assert ranked.plan_hash() == planned.plan_hash()  # the alternatives are not part of the plan the Verifier checks
    assert ranked.bump().ranked_stays == ranked.ranked_stays
