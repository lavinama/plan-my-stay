"""Small builders that mutate the demo plan without touching it (every model is frozen)."""

from collections.abc import Iterable
from typing import Any

from plan_my_stay import demo
from plan_my_stay.models import Conflict, Leg, ProposalItem
from plan_my_stay.state import TripState

REST_ID = "rest-urban-vegan-kitchen"
GYM_ID = "gym-example-hiit"


def rules_of(conflicts: Iterable[Conflict]) -> list[str]:
    return [c.rule for c in conflicts]


def only(conflicts: Iterable[Conflict], rule: str) -> list[Conflict]:
    return [c for c in conflicts if c.rule == rule]


def with_item(state: TripState, item_id: str, **changes: Any) -> TripState:
    """The plan with one item's fields replaced."""
    entries = tuple(
        e.model_copy(update={"item": e.item.model_copy(update=changes)}) if e.item.item_id == item_id else e
        for e in state.entries
    )
    return state.bump(entries=entries)


def item_of(state: TripState, item_id: str) -> ProposalItem:
    return next(e.item for e in state.entries if e.item.item_id == item_id)


def with_leg(state: TripState, from_id: str, to_id: str, **changes: Any) -> TripState:
    """The plan with one leg's fields replaced."""
    legs = tuple(
        leg.model_copy(update=changes) if (leg.from_id, leg.to_id) == (from_id, to_id) else leg for leg in state.legs
    )
    return state.bump(legs=legs)


def without_leg(state: TripState, from_id: str, to_id: str) -> TripState:
    return state.bump(legs=tuple(leg for leg in state.legs if (leg.from_id, leg.to_id) != (from_id, to_id)))


def leg_of(state: TripState, from_id: str, to_id: str) -> Leg:
    return next(leg for leg in state.legs if (leg.from_id, leg.to_id) == (from_id, to_id))


def clean_plan() -> TripState:
    """The demo plan with a known fee basis: the Verifier finds nothing."""
    return demo.demo_state_with_plan(stay_basis="total_incl_fees")
