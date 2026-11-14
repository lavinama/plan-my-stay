"""Which trips the Concierge asks Routing for, and where the stay is."""

import pytest

from plan_my_stay import demo
from plan_my_stay.concierge.legs import locate_stay, plan_legs
from plan_my_stay.enums import LocationPrecision, Seat
from plan_my_stay.messages.payloads import StayPickPayload
from plan_my_stay.models import Anchor, LatLng, PlanEntry

CHELSEA = LatLng(lat=40.747, lng=-74.0)


def planned_state():
    """The demo plan with Routing's answers removed: what the Concierge holds just before it asks for legs."""
    return demo.demo_state_with_plan().bump(legs=())


def pairs(specs):
    return [(s.from_id, s.to_id) for s in specs]


def test_a_day_runs_event_to_event_and_ends_at_the_stay_and_the_next_morning_starts_there():
    specs = plan_legs(planned_state(), CHELSEA)

    assert pairs(specs) == [
        ("A", "B"),
        ("B", "gym-example-hiit"),
        ("gym-example-hiit", "rest-urban-vegan-kitchen"),
        ("rest-urban-vegan-kitchen", "stay"),
        ("stay", "C"),
    ]


def test_each_leg_leaves_when_the_previous_event_ends():
    specs = plan_legs(planned_state(), CHELSEA)

    assert specs[0].depart_at == demo.at(17, 12)  # A ends at 12:00
    assert specs[1].depart_at == demo.at(17, 17)  # B ends at 17:00
    assert specs[2].depart_at == demo.at(17, 18, 15)  # the workout ends at 18:15
    assert specs[3].depart_at == demo.at(17, 19, 45)  # dinner ends at 19:45


def test_the_morning_trip_from_the_stay_leaves_an_hour_before_the_first_event():
    specs = plan_legs(planned_state(), CHELSEA)

    assert specs[-1].depart_at == demo.at(18, 9)  # C starts at 10:00


def test_the_traveler_goes_straight_to_the_first_event_on_check_in_day():
    specs = plan_legs(planned_state(), CHELSEA)

    assert ("stay", "A") not in pairs(specs)


def test_places_carry_coordinates_only_never_an_address():
    for spec in plan_legs(planned_state(), CHELSEA):
        for place in (spec.origin, spec.destination):
            assert place.lat is not None and place.lng is not None
            assert place.address is None


def test_the_stay_end_of_a_leg_uses_the_stay_location():
    specs = plan_legs(planned_state(), CHELSEA)
    to_stay = specs[3]
    from_stay = specs[4]

    assert (to_stay.destination.lat, to_stay.destination.lng) == (CHELSEA.lat, CHELSEA.lng)
    assert (from_stay.origin.lat, from_stay.origin.lng) == (CHELSEA.lat, CHELSEA.lng)


def test_without_a_stay_location_the_stay_legs_are_skipped():
    specs = plan_legs(planned_state(), None)

    assert pairs(specs) == [("A", "B"), ("B", "gym-example-hiit"), ("gym-example-hiit", "rest-urban-vegan-kitchen")]


def test_two_events_at_the_same_place_need_no_leg():
    state = planned_state()
    gym = state.entries[0]
    same_as_b = gym.item.model_copy(update={"lat": 40.7502, "lng": -73.9987})
    state = state.bump(entries=(PlanEntry(agent=Seat.GYM, item=same_as_b), state.entries[1]))

    assert ("B", "gym-example-hiit") not in pairs(plan_legs(state, CHELSEA))


def test_an_item_with_no_coordinates_or_no_time_is_left_out():
    state = planned_state()
    gym, restaurant = state.entries
    no_coords = restaurant.item.model_copy(update={"lat": None, "lng": None, "location_precision": LocationPrecision.ZONE})
    no_time = gym.item.model_copy(update={"when": None})
    state = state.bump(entries=(PlanEntry(agent=Seat.GYM, item=no_time), PlanEntry(agent=Seat.RESTAURANT, item=no_coords)))

    assert pairs(plan_legs(state, CHELSEA)) == [("A", "B"), ("B", "stay"), ("stay", "C")]


def test_legs_do_not_depend_on_the_order_the_entries_arrived_in():
    state = planned_state()
    flipped = state.bump(entries=tuple(reversed(state.entries)))

    assert pairs(plan_legs(flipped, CHELSEA)) == pairs(plan_legs(state, CHELSEA))


def test_check_out_day_starts_at_the_stay_and_does_not_return_to_it():
    state = planned_state()  # the demo has no Thursday meeting, so add one
    d = Anchor(id="D", lat=40.73, lng=-73.99, start=demo.at(19, 9), end=demo.at(19, 10), counts_for_stay=True)
    state = state.bump(anchors=(*state.anchors, d))

    specs = plan_legs(state, CHELSEA)

    assert ("stay", "D") in pairs(specs)
    assert not any(s.from_id == "D" for s in specs)


def test_the_stay_legs_are_skipped_when_there_is_no_stay_chosen():
    state = planned_state().bump(stay=None)

    assert "stay" not in {x for s in plan_legs(state, CHELSEA) for x in (s.from_id, s.to_id)}


# --- locating the stay ------------------------------------------------------------------------------------------------


def test_the_stay_s_own_coordinates_come_first():
    stay = demo.demo_stay().model_copy(update={"lat": 40.7, "lng": -74.0})
    pick = StayPickPayload(stay_id=stay.stay_id, zone_id="z1", lat=41.0, lng=-75.0)

    assert locate_stay(stay, pick, demo.demo_zones()) == LatLng(lat=40.7, lng=-74.0)


def test_then_the_stay_pick_for_that_stay():
    stay = demo.demo_stay()
    pick = StayPickPayload(stay_id=stay.stay_id, zone_id="z1", lat=40.74, lng=-74.0)

    assert locate_stay(stay, pick, demo.demo_zones()) == LatLng(lat=40.74, lng=-74.0)


def test_a_pick_for_a_different_stay_is_ignored():
    stay = demo.demo_stay()
    pick = StayPickPayload(stay_id="some-other-stay", zone_id="z1", lat=40.0, lng=-70.0)

    located = locate_stay(stay, pick, demo.demo_zones())

    assert located != LatLng(lat=40.0, lng=-70.0)


def test_then_the_centre_of_its_zone():
    located = locate_stay(demo.demo_stay(), None, demo.demo_zones())  # z1 is Chelsea

    assert located.lat == pytest.approx((40.756 + 40.738) / 2)
    assert located.lng == pytest.approx((-73.99 + -74.01) / 2)


def test_a_stay_in_an_unknown_zone_has_no_location():
    stay = demo.demo_stay().model_copy(update={"zone_id": "zzz"})

    assert locate_stay(stay, None, demo.demo_zones()) is None


def test_planning_legs_never_mutates_the_state():
    state = planned_state()
    before = state.model_dump_json()
    plan_legs(state, CHELSEA)

    assert state.model_dump_json() == before
