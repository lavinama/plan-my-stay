"""The Verifier's timing rules on mutated copies of the demo plan: hours, slot windows and overlaps."""

import datetime as dt

from plan_my_stay import demo
from plan_my_stay.models import Conflict, When, Window
from plan_my_stay.verifier import checks
from plan_my_stay.verifier.checks import check_plan
from tests.verifier.helpers import GYM_ID, REST_ID, clean_plan, rules_of, with_item

RESERVE = 0.15
TUE = dt.date(2026, 11, 17)

# --- the clean demo plan -------------------------------------------------------------------------


def test_a_clean_plan_has_no_conflicts():
    assert check_plan(clean_plan(), RESERVE) == ()


def test_the_demo_plan_with_an_unknown_fee_basis_only_warns_about_the_fee():
    conflicts = check_plan(demo.demo_state_with_plan(), RESERVE)

    assert rules_of(conflicts) == ["fee_basis_unknown"]


def test_every_conflict_is_a_conflict_model():
    assert all(isinstance(c, Conflict) for c in check_plan(demo.demo_state_with_plan(), RESERVE))


# --- open_during_slot and unknown_hours ------------------------------------------------------------


def test_a_restaurant_that_closes_before_dinner_ends_blocks():
    state = with_item(clean_plan(), REST_ID, hours={"Tue": "11:00-19:30"})  # dinner runs 18:30 to 19:45

    conflicts = checks.check_hours(state)

    assert rules_of(conflicts) == ["open_during_slot"]
    assert conflicts[0].item_id == REST_ID
    assert "19:30" in conflicts[0].detail and "19:45" in conflicts[0].detail
    assert conflicts[0].suggestion


def test_the_closing_minute_still_counts_as_open():
    state = with_item(clean_plan(), REST_ID, hours={"Tue": "11:00-19:45"})

    assert checks.check_hours(state) == ()


def test_a_place_open_past_midnight_the_night_before_is_not_blocked():
    state = with_item(clean_plan(), REST_ID, hours={"Mon": "18:00-02:00", "Tue": "11:00-22:00"})

    assert checks.check_hours(state) == ()


def test_split_hours_with_a_break_over_the_slot_block():
    state = with_item(clean_plan(), REST_ID, hours={"Tue": ("11:00-18:45", "19:30-22:00")})

    assert rules_of(checks.check_hours(state)) == ["open_during_slot"]


def test_missing_hours_warn_instead_of_blocking():
    state = with_item(clean_plan(), GYM_ID, hours=None)

    conflicts = checks.check_hours(state)

    assert rules_of(conflicts) == ["unknown_hours"]
    assert conflicts[0].item_id == GYM_ID


def test_hours_without_that_weekday_warn():
    state = with_item(clean_plan(), REST_ID, hours={"Wed": "11:00-22:00"})

    assert rules_of(checks.check_hours(state)) == ["unknown_hours"]


def test_an_item_without_a_time_is_not_checked():
    state = with_item(clean_plan(), REST_ID, hours=None, when=None)

    assert checks.check_hours(state) == ()


# --- outside_slot_window ---------------------------------------------------------------------------


def _with_slot_window(state, slot_id: str, start: str, end: str):
    slots = tuple(s.model_copy(update={"window": Window(start=start, end=end)}) if s.id == slot_id else s for s in state.slots)
    return state.bump(slots=slots)


def test_an_item_ending_after_its_slot_window_blocks():
    state = _with_slot_window(clean_plan(), "s1", "17:30", "19:00")  # dinner runs to 19:45

    conflicts = checks.check_slot_windows(state)

    assert rules_of(conflicts) == ["outside_slot_window"]
    assert conflicts[0].item_id == REST_ID
    assert "s1" in conflicts[0].detail and "19:00" in conflicts[0].detail


def test_an_item_starting_before_its_slot_window_blocks():
    state = _with_slot_window(clean_plan(), "s1", "19:00", "21:00")

    assert rules_of(checks.check_slot_windows(state)) == ["outside_slot_window"]


def test_an_item_on_another_day_than_its_slot_blocks():
    state = with_item(clean_plan(), REST_ID, when=When(date=dt.date(2026, 11, 18), start="18:30", end="19:45"))

    assert rules_of(checks.check_slot_windows(state)) == ["outside_slot_window"]


def test_an_item_exactly_filling_its_slot_window_is_inside():
    state = _with_slot_window(clean_plan(), "s1", "18:30", "19:45")

    assert checks.check_slot_windows(state) == ()


def test_an_item_with_no_slot_is_not_window_checked():
    state = with_item(clean_plan(), REST_ID, slot_id=None)

    assert checks.check_slot_windows(state) == ()


def test_an_item_naming_an_unknown_slot_is_skipped_not_blocked():
    state = with_item(clean_plan(), REST_ID, slot_id="no-such-slot")

    assert checks.check_slot_windows(state) == ()


# --- overlaps_event --------------------------------------------------------------------------------


def test_an_item_overlapping_an_anchor_blocks_the_item():
    # Meeting B is Tue 16:00 to 17:00.
    state = with_item(clean_plan(), GYM_ID, when=When(date=TUE, start="16:30", end="17:15"))

    conflicts = checks.check_overlaps(state)

    assert rules_of(conflicts) == ["overlaps_event"]
    assert conflicts[0].item_id == GYM_ID
    assert "B" in conflicts[0].detail


def test_two_overlapping_items_give_one_conflict_on_the_later_item():
    state = with_item(clean_plan(), GYM_ID, when=When(date=TUE, start="17:30", end="18:45"))  # dinner starts 18:30

    conflicts = checks.check_overlaps(state)

    assert [c.item_id for c in conflicts] == [REST_ID]
    assert "Example HIIT Studio" in conflicts[0].detail


def test_touching_events_do_not_overlap():
    state = with_item(clean_plan(), GYM_ID, when=When(date=TUE, start="17:30", end="18:30"))  # dinner starts 18:30

    assert checks.check_overlaps(state) == ()


def test_one_item_overlapping_two_events_gets_one_conflict_naming_both():
    state = with_item(clean_plan(), GYM_ID, when=When(date=TUE, start="16:30", end="17:30"))  # overlaps meeting B
    state = with_item(state, REST_ID, when=When(date=TUE, start="16:45", end="18:00"))  # overlaps B and the workout

    conflicts = checks.check_overlaps(state)

    assert sorted(c.item_id for c in conflicts) == [GYM_ID, REST_ID]
    dinner = next(c for c in conflicts if c.item_id == REST_ID)
    assert "B" in dinner.detail and "Example HIIT Studio" in dinner.detail


def test_items_on_different_days_never_overlap():
    state = with_item(clean_plan(), GYM_ID, when=When(date=dt.date(2026, 11, 18), start="18:30", end="19:45"))

    assert checks.check_overlaps(state) == ()


