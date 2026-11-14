"""The Verifier's budget rules, the severity table, who owns a conflict, and the whole run."""

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import Mode, Seat, VerdictStatus
from plan_my_stay.models import Conflict, CostBasis
from plan_my_stay.verifier import checks
from plan_my_stay.verifier.checks import SEVERITY, check_plan, is_blocking, owner_of
from tests.verifier.helpers import GYM_ID, REST_ID, clean_plan, only, rules_of, with_item, with_leg

RESERVE = 0.15

# --- over_budget and fee_basis_unknown ---------------------------------------------------------------


def test_a_stay_over_budget_with_a_known_fee_basis_blocks_without_a_fee_warning():
    state = demo.demo_state_with_plan(stay_total=1100.0, stay_basis=CostBasis.TOTAL_INCL_FEES)

    conflicts = checks.check_stay_budget(state, RESERVE)

    assert rules_of(conflicts) == ["over_budget"]
    assert conflicts[0].item_id == "stay-chelsea-1"
    assert "1,100.00" in conflicts[0].detail and "1,000.00" in conflicts[0].detail
    assert conflicts[0].suggestion


def test_a_stay_exactly_on_the_budget_is_within_it():
    state = demo.demo_state_with_plan(stay_total=1000.0, stay_basis=CostBasis.TOTAL_INCL_FEES)

    assert checks.check_stay_budget(state, RESERVE) == ()


def test_the_fee_reserve_can_tip_a_stay_over_budget():
    # 900 looks fine, but the fee basis is unknown: 900 x 1.15 = 1035.
    state = demo.demo_state_with_plan(stay_total=900.0, stay_basis=CostBasis.CARD_TOTAL_FEES_UNKNOWN)

    conflicts = checks.check_stay_budget(state, RESERVE)

    assert sorted(rules_of(conflicts)) == ["fee_basis_unknown", "over_budget"]
    over = only(conflicts, "over_budget")[0]
    assert "1,035.00" in over.detail and "15%" in over.detail


def test_a_smaller_reserve_keeps_the_same_stay_within_budget():
    state = demo.demo_state_with_plan(stay_total=900.0, stay_basis=CostBasis.CARD_TOTAL_FEES_UNKNOWN)

    assert rules_of(checks.check_stay_budget(state, 0.05)) == ["fee_basis_unknown"]  # 900 x 1.05 = 945


def test_a_known_fee_basis_gets_no_reserve_so_the_same_total_is_within_budget():
    state = demo.demo_state_with_plan(stay_total=900.0, stay_basis=CostBasis.TOTAL_INCL_FEES)

    assert checks.check_stay_budget(state, RESERVE) == ()


def test_an_unknown_basis_gets_the_reserve_and_the_warning_too():
    state = demo.demo_state_with_plan(stay_total=800.0, stay_basis=CostBasis.UNKNOWN)

    conflicts = checks.check_stay_budget(state, RESERVE)

    assert rules_of(conflicts) == ["fee_basis_unknown"]
    assert "920.00" in conflicts[0].detail


def test_a_plan_with_no_stay_has_no_budget_conflicts():
    assert checks.check_stay_budget(demo.demo_state(), RESERVE) == ()


def test_a_stay_priced_in_another_currency_blocks_because_it_cannot_be_compared():
    state = demo.demo_state_with_plan(stay_basis=CostBasis.TOTAL_INCL_FEES)
    state = state.bump(stay=state.stay.model_copy(update={"currency": "EUR"}))

    conflicts = checks.check_stay_budget(state, RESERVE)

    assert rules_of(conflicts) == ["over_budget"]
    assert "EUR" in conflicts[0].detail and "USD" in conflicts[0].detail


def test_a_negative_fee_reserve_is_rejected():
    with pytest.raises(ValueError):
        checks.check_stay_budget(demo.demo_state_with_plan(), -0.1)


# --- severity, ownership and the whole run -----------------------------------------------------------


def test_severity_follows_the_rules_table():
    assert SEVERITY == {
        "open_during_slot": VerdictStatus.BLOCK,
        "unknown_hours": VerdictStatus.WARN,
        "outside_slot_window": VerdictStatus.BLOCK,
        "overlaps_event": VerdictStatus.BLOCK,
        "travel_time": VerdictStatus.BLOCK,
        "missing_leg": VerdictStatus.WARN,
        "over_budget": VerdictStatus.BLOCK,
        "fee_basis_unknown": VerdictStatus.WARN,
    }


def test_is_blocking_uses_the_rule():
    assert is_blocking(Conflict(rule="open_during_slot", item_id="x", detail="d")) is True
    assert is_blocking(Conflict(rule="unknown_hours", item_id="x", detail="d")) is False


def test_an_unknown_rule_is_not_blocking_so_a_newer_rule_name_never_blocks_by_accident():
    assert is_blocking(Conflict(rule="from_the_future", item_id="x", detail="d")) is False


def test_an_item_conflict_goes_to_the_agent_that_owns_the_item():
    state = clean_plan()

    assert owner_of(Conflict(rule="open_during_slot", item_id=REST_ID, detail="d"), state) is Seat.RESTAURANT
    assert owner_of(Conflict(rule="open_during_slot", item_id=GYM_ID, detail="d"), state) is Seat.GYM


def test_stay_and_budget_conflicts_go_to_the_stay_agent():
    state = clean_plan()

    assert owner_of(Conflict(rule="over_budget", item_id=state.stay.stay_id, detail="d"), state) is Seat.STAY
    assert owner_of(Conflict(rule="fee_basis_unknown", item_id="stay", detail="d"), state) is Seat.STAY
    assert owner_of(Conflict(rule="over_budget", item_id="whatever", detail="d"), state) is Seat.STAY


def test_any_conflict_on_the_stays_id_goes_to_the_stay_agent():
    state = clean_plan()

    assert owner_of(Conflict(rule="missing_leg", item_id=state.stay.stay_id, detail="d"), state) is Seat.STAY


def test_an_item_with_no_time_is_not_an_event_for_overlaps_or_missing_legs():
    state = with_item(clean_plan(), REST_ID, when=None)

    assert checks.check_overlaps(state) == ()
    # Without the dinner the workout and meeting C become neighbours with no leg between them: that is the warning.
    assert [(c.rule, c.item_id) for c in checks.check_missing_legs(state)] == [("missing_leg", "C")]


def test_an_anchor_conflict_goes_to_the_concierge():
    assert owner_of(Conflict(rule="travel_time", item_id="C", detail="d"), clean_plan()) is Seat.CONCIERGE


def test_a_conflict_on_an_id_nobody_owns_goes_to_the_concierge():
    assert owner_of(Conflict(rule="open_during_slot", item_id="ghost", detail="d"), clean_plan()) is Seat.CONCIERGE


def test_a_travel_time_conflict_goes_to_the_owner_of_the_later_event():
    state = with_leg(clean_plan(), GYM_ID, REST_ID, minutes_by_mode={Mode.WALKING: 20.0}, chosen_mode=Mode.WALKING)

    conflict = checks.check_travel_times(state)[0]

    assert owner_of(conflict, state) is Seat.RESTAURANT


def test_check_plan_runs_every_rule_and_is_deterministic():
    state = with_item(clean_plan(), REST_ID, hours={"Tue": "11:00-19:30"})
    state = with_item(state, GYM_ID, hours=None)
    state = state.bump(stay=state.stay.model_copy(update={"total_price": 1500.0}))

    first = check_plan(state, RESERVE)

    assert sorted(rules_of(first)) == ["open_during_slot", "over_budget", "unknown_hours"]
    assert check_plan(state, RESERVE) == first


def test_check_plan_does_not_change_the_state():
    state = with_item(clean_plan(), REST_ID, hours={"Tue": "11:00-19:30"})
    before = state.model_dump_json()

    check_plan(state, RESERVE)

    assert state.model_dump_json() == before
