"""Trip state (versioned, immutable, hashable) and the budget check with a fee reserve."""

import pytest
from pydantic import ValidationError

from plan_my_stay import demo
from plan_my_stay.budget import check_budget
from plan_my_stay.models import CostBasis


def test_demo_state_has_the_three_demo_meetings_and_marks_a_as_before_check_in():
    state = demo.demo_state()

    assert [a.id for a in state.anchors] == ["A", "B", "C"]
    assert [a.counts_for_stay for a in state.anchors] == [False, True, True]
    assert state.budget.amount == 1000
    assert state.state_version == 0


def test_state_is_frozen():
    state = demo.demo_state()
    with pytest.raises(ValidationError):
        state.state_version = 5


def test_bump_returns_a_new_state_with_a_higher_version_and_leaves_the_original_alone():
    state = demo.demo_state()
    newer = state.bump(round=1)

    assert newer is not state
    assert (state.state_version, state.round) == (0, 0)
    assert (newer.state_version, newer.round) == (1, 1)


def test_bump_validates_what_it_is_given():
    with pytest.raises(ValidationError):
        demo.demo_state().bump(round=3)  # revisions are capped at 2


def test_plan_hash_is_stable_and_ignores_version_and_open_conflicts():
    state = demo.demo_state_with_plan()

    assert state.plan_hash() == demo.demo_state_with_plan().plan_hash()
    assert state.bump().plan_hash() == state.plan_hash()
    assert len(state.plan_hash()) == 64


def test_plan_hash_changes_when_the_plan_changes():
    state = demo.demo_state_with_plan()
    other_stay = state.stay.model_copy(update={"total_price": state.stay.total_price + 1})

    assert state.bump(stay=other_stay).plan_hash() != state.plan_hash()
    assert state.bump(entries=state.entries[:-1]).plan_hash() != state.plan_hash()


def test_known_fee_basis_has_no_reserve():
    check = check_budget(900, CostBasis.TOTAL_INCL_FEES, budget=1000, fee_reserve_pct=0.15)

    assert (check.effective_total, check.reserve_applied, check.within_budget) == (900, False, True)


def test_unknown_fee_basis_adds_the_reserve_and_can_push_a_stay_over_budget():
    check = check_budget(900, CostBasis.CARD_TOTAL_FEES_UNKNOWN, budget=1000, fee_reserve_pct=0.15)

    assert check.effective_total == pytest.approx(1035)
    assert check.reserve_applied is True
    assert check.within_budget is False


def test_a_stay_exactly_at_the_budget_is_within_it():
    assert check_budget(1000, CostBasis.TOTAL_INCL_FEES, budget=1000, fee_reserve_pct=0.15).within_budget


def test_negative_reserve_is_rejected():
    with pytest.raises(ValueError):
        check_budget(900, CostBasis.UNKNOWN, budget=1000, fee_reserve_pct=-0.1)
