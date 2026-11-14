"""The Verifier's leg rules: travel_time and missing_leg."""

from plan_my_stay.enums import Mode, Seat
from plan_my_stay.verifier import checks
from plan_my_stay.verifier.checks import owner_of
from tests.verifier.helpers import (
    GYM_ID,
    REST_ID,
    clean_plan,
    item_of,
    leg_of,
    rules_of,
    with_item,
    with_leg,
    without_leg,
)

# --- travel_time -----------------------------------------------------------------------------------


def test_a_leg_too_slow_for_the_gap_blocks_the_later_event():
    # Gym ends 18:15, dinner starts 18:30. The leg leaves 18:20 and walking now takes 20 minutes.
    state = with_leg(clean_plan(), GYM_ID, REST_ID, minutes_by_mode={Mode.WALKING: 20.0}, chosen_mode=Mode.WALKING)

    conflicts = checks.check_travel_times(state)

    assert rules_of(conflicts) == ["travel_time"]
    assert conflicts[0].item_id == REST_ID
    assert "18:40" in conflicts[0].detail and "18:30" in conflicts[0].detail


def test_the_chosen_mode_decides_not_the_fastest():
    state = with_leg(
        clean_plan(), GYM_ID, REST_ID, minutes_by_mode={Mode.WALKING: 40.0, Mode.DRIVING: 5.0}, chosen_mode=Mode.WALKING
    )

    assert rules_of(checks.check_travel_times(state)) == ["travel_time"]


def test_arriving_exactly_at_the_start_is_fine():
    state = with_leg(clean_plan(), GYM_ID, REST_ID, minutes_by_mode={Mode.WALKING: 10.0}, chosen_mode=Mode.WALKING)

    assert checks.check_travel_times(state) == ()  # 18:20 + 10 = 18:30


def test_leaving_before_the_from_event_ends_blocks():
    # The leg leaves the gym at 18:10 but the workout runs to 18:15.
    depart = leg_of(clean_plan(), GYM_ID, REST_ID).depart_at.replace(minute=10)
    state = with_leg(clean_plan(), GYM_ID, REST_ID, depart_at=depart)

    conflicts = checks.check_travel_times(state)

    assert rules_of(conflicts) == ["travel_time"]
    assert "18:15" in conflicts[0].detail


def test_leaving_the_restaurant_before_it_ends_blocks_the_restaurant():
    depart = leg_of(clean_plan(), REST_ID, "stay").depart_at.replace(hour=19, minute=30)  # dinner ends 19:45
    state = with_leg(clean_plan(), REST_ID, "stay", depart_at=depart)

    conflicts = checks.check_travel_times(state)

    assert [c.item_id for c in conflicts] == [REST_ID]


def test_a_leg_from_the_stay_arriving_after_the_meeting_starts_blocks_the_anchor():
    state = with_leg(clean_plan(), "stay", "C", minutes_by_mode={Mode.TRANSIT: 70.0}, chosen_mode=Mode.TRANSIT)  # 09:00 + 70 > 10:00

    conflicts = checks.check_travel_times(state)

    assert [c.item_id for c in conflicts] == ["C"]
    assert owner_of(conflicts[0], state) is Seat.CONCIERGE


def test_a_leg_failing_both_ways_is_one_conflict():
    depart = leg_of(clean_plan(), GYM_ID, REST_ID).depart_at.replace(minute=10)
    state = with_leg(
        clean_plan(), GYM_ID, REST_ID, depart_at=depart, minutes_by_mode={Mode.WALKING: 60.0}, chosen_mode=Mode.WALKING
    )

    assert len(checks.check_travel_times(state)) == 1


def test_a_leg_between_unknown_ids_is_ignored():
    state = clean_plan()
    ghost = leg_of(state, "A", "B").model_copy(update={"from_id": "ghost-1", "to_id": "ghost-2"})

    assert checks.check_travel_times(state.bump(legs=state.legs + (ghost,))) == ()


# --- missing_leg -----------------------------------------------------------------------------------


def test_a_leg_with_no_chosen_mode_warns():
    state = with_leg(clean_plan(), "A", "B", chosen_mode=None)

    conflicts = checks.check_travel_times(state)

    assert rules_of(conflicts) == ["missing_leg"]
    assert conflicts[0].item_id == "B"


def test_a_leg_whose_chosen_mode_has_no_minutes_warns():
    state = with_leg(
        clean_plan(), "A", "B", minutes_by_mode={Mode.TRANSIT: None, Mode.WALKING: 70.0}, chosen_mode=Mode.TRANSIT
    )

    assert rules_of(checks.check_travel_times(state)) == ["missing_leg"]


def test_a_chosen_mode_missing_from_the_minutes_warns():
    state = with_leg(clean_plan(), "A", "B", minutes_by_mode={Mode.WALKING: 70.0}, chosen_mode=Mode.TRANSIT)

    assert rules_of(checks.check_travel_times(state)) == ["missing_leg"]


def test_a_null_leg_to_the_stay_is_attributed_to_the_event_it_leaves():
    state = with_leg(clean_plan(), REST_ID, "stay", chosen_mode=None)

    assert [c.item_id for c in checks.check_travel_times(state)] == [REST_ID]


def test_far_apart_consecutive_events_with_no_leg_warn():
    state = without_leg(clean_plan(), GYM_ID, REST_ID)  # about 0.5 km apart

    conflicts = checks.check_missing_legs(state)

    assert rules_of(conflicts) == ["missing_leg"]
    assert conflicts[0].item_id == REST_ID


def test_a_leg_in_the_other_direction_still_counts():
    state = without_leg(clean_plan(), GYM_ID, REST_ID)
    reverse = leg_of(clean_plan(), GYM_ID, REST_ID).model_copy(update={"from_id": REST_ID, "to_id": GYM_ID})

    assert checks.check_missing_legs(state.bump(legs=state.legs + (reverse,))) == ()


def test_events_within_300_metres_need_no_leg():
    gym = item_of(clean_plan(), GYM_ID)
    state = with_item(without_leg(clean_plan(), GYM_ID, REST_ID), REST_ID, lat=gym.lat + 0.001, lng=gym.lng)  # ~110 m

    assert checks.check_missing_legs(state) == ()


def test_events_just_over_300_metres_need_a_leg():
    gym = item_of(clean_plan(), GYM_ID)
    state = with_item(without_leg(clean_plan(), GYM_ID, REST_ID), REST_ID, lat=gym.lat + 0.0028, lng=gym.lng)  # ~311 m

    assert rules_of(checks.check_missing_legs(state)) == ["missing_leg"]


def test_two_legs_through_the_stay_connect_dinner_to_the_next_meeting():
    # The clean plan has dinner -> stay and stay -> C but no dinner -> C leg, and the two are far apart.
    assert checks.check_missing_legs(clean_plan()) == ()


def test_without_the_stay_legs_dinner_to_the_next_meeting_is_missing():
    state = without_leg(without_leg(clean_plan(), REST_ID, "stay"), "stay", "C")

    conflicts = checks.check_missing_legs(state)

    assert [c.item_id for c in conflicts] == ["C"]


def test_a_missing_leg_to_an_event_with_no_coordinates_warns():
    state = with_item(without_leg(clean_plan(), GYM_ID, REST_ID), REST_ID, lat=None, lng=None, location_precision="zone")

    conflicts = checks.check_missing_legs(state)

    assert rules_of(conflicts) == ["missing_leg"]
    assert "location" in conflicts[0].detail


def test_a_null_leg_is_not_also_reported_as_no_leg():
    state = with_leg(clean_plan(), GYM_ID, REST_ID, chosen_mode=None)

    assert checks.check_missing_legs(state) == ()
    assert rules_of(checks.check_travel_times(state)) == ["missing_leg"]


def test_a_single_event_needs_no_leg():
    state = clean_plan().bump(anchors=clean_plan().anchors[:1], entries=(), legs=())

    assert checks.check_missing_legs(state) == ()


