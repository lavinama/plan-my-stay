"""VerifierSeat: the message in, the verdict and conflict messages out."""

import json

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Mode, Seat, VerdictStatus
from plan_my_stay.messages import Message, build_message, parse_message, serialize
from plan_my_stay.messages.examples import valid_examples
from plan_my_stay.messages.payloads import ErrorPayload, ProposalPayload, VerdictPayload
from plan_my_stay.models import Conflict, CostBasis, When
from plan_my_stay.seat import Seat as SeatProtocol
from plan_my_stay.state import TripState
from plan_my_stay.verifier import VerifierSeat
from tests.verifier.helpers import GYM_ID, REST_ID, clean_plan, rules_of, with_item, with_leg


def review_message(state: TripState, *, state_version: int | None = None, trip_id: str | None = None) -> Message:
    """What the Concierge sends: the merged plan as a proposal with agent=concierge."""
    return build_message(
        type=MessageType.PROPOSAL,
        sender=Seat.CONCIERGE,
        to=[Seat.VERIFIER],
        trip_id=trip_id or state.trip_id,
        user_id=state.user_id,
        state_version=state.state_version if state_version is None else state_version,
        summary=f"Merged plan for review (state_version {state.state_version})",
        payload=ProposalPayload(agent=Seat.CONCIERGE, state=state),
    )


def closing_early(state: TripState) -> TripState:
    return with_item(state, REST_ID, hours={"Tue": "11:00-19:30"})  # dinner runs 18:30 to 19:45


@pytest.fixture
def seat() -> VerifierSeat:
    return VerifierSeat()


# --- the seat itself -------------------------------------------------------------------------------


def test_it_satisfies_the_seat_protocol(seat):
    assert isinstance(seat, SeatProtocol)


def test_the_default_fee_reserve_is_15_percent():
    assert VerifierSeat().fee_reserve_pct == 0.15


def test_a_negative_fee_reserve_is_rejected_at_construction():
    with pytest.raises(ValueError):
        VerifierSeat(fee_reserve_pct=-0.01)


# --- clear and warn ----------------------------------------------------------------------------------


def test_a_clean_plan_is_cleared_with_its_plan_hash(seat):
    state = clean_plan()
    incoming = review_message(state)

    out = seat.handle(incoming)

    assert len(out) == 1
    verdict = out[0]
    assert verdict.type is MessageType.VERDICT
    assert verdict.sender is Seat.VERIFIER
    assert verdict.to == (Seat.CONCIERGE,)
    assert verdict.reply_to == incoming.message_id
    assert isinstance(verdict.payload, VerdictPayload)
    assert verdict.payload.status is VerdictStatus.CLEAR
    assert verdict.payload.plan_hash == state.plan_hash()
    assert verdict.payload.conflicts == ()


def test_the_demo_plan_with_an_unknown_fee_basis_warns_with_the_hash_and_the_warning(seat):
    state = demo.demo_state_with_plan()

    out = seat.handle(review_message(state))

    assert len(out) == 1
    payload = out[0].payload
    assert payload.status is VerdictStatus.WARN
    assert payload.plan_hash == state.plan_hash()
    assert rules_of(payload.conflicts) == ["fee_basis_unknown"]


def test_a_warning_sends_no_conflict_messages(seat):
    out = seat.handle(review_message(with_item(clean_plan(), GYM_ID, hours=None)))

    assert [m.type for m in out] == [MessageType.VERDICT]
    assert out[0].payload.status is VerdictStatus.WARN
    assert rules_of(out[0].payload.conflicts) == ["unknown_hours"]


def test_the_envelope_copies_the_incoming_trip_user_and_state_version(seat):
    state = clean_plan().bump()
    incoming = review_message(state, state_version=7, trip_id="room-42")

    (verdict,) = seat.handle(incoming)

    assert (verdict.trip_id, verdict.user_id, verdict.state_version) == ("room-42", state.user_id, 7)


def test_the_plan_hash_changes_when_the_plan_changes(seat):
    state = clean_plan()
    changed = with_item(state, GYM_ID, when=When(date=state.entries[0].item.when.date, start="17:30", end="18:10"))

    first = seat.handle(review_message(state))[0].payload.plan_hash
    second = seat.handle(review_message(changed))[0].payload.plan_hash

    assert first == state.plan_hash() and second == changed.plan_hash()
    assert first != second


def test_the_hash_ignores_a_state_version_bump(seat):
    state = clean_plan()

    assert seat.handle(review_message(state.bump()))[0].payload.plan_hash == state.plan_hash()


# --- block -------------------------------------------------------------------------------------------


def test_a_restaurant_closing_before_dinner_ends_blocks_without_a_hash(seat):
    state = closing_early(clean_plan())
    incoming = review_message(state)

    out = seat.handle(incoming)

    verdict = out[0]
    assert verdict.type is MessageType.VERDICT
    assert verdict.payload.status is VerdictStatus.BLOCK
    assert verdict.payload.plan_hash is None
    assert rules_of(verdict.payload.conflicts) == ["open_during_slot"]
    assert verdict.to == (Seat.CONCIERGE,)
    assert verdict.reply_to == incoming.message_id


def test_a_block_sends_one_conflict_message_to_the_owner_of_each_blocker(seat):
    incoming = review_message(closing_early(clean_plan()))

    out = seat.handle(incoming)

    conflicts = [m for m in out if m.type is MessageType.CONFLICT]
    assert len(conflicts) == 1
    message = conflicts[0]
    assert message.sender is Seat.VERIFIER
    assert message.to == (Seat.RESTAURANT,)
    assert message.reply_to == incoming.message_id
    assert isinstance(message.payload, Conflict)
    assert (message.payload.rule, message.payload.item_id) == ("open_during_slot", REST_ID)


def test_each_blocker_goes_to_its_own_owner(seat):
    state = closing_early(clean_plan())
    state = with_item(state, GYM_ID, hours={"Tue": "06:00-17:45"})  # gym closes mid-workout
    state = state.bump(stay=state.stay.model_copy(update={"total_price": 1500.0}))

    out = seat.handle(review_message(state))

    routed = {(m.to[0], m.payload.rule) for m in out if m.type is MessageType.CONFLICT}
    assert routed == {
        (Seat.RESTAURANT, "open_during_slot"),
        (Seat.GYM, "open_during_slot"),
        (Seat.STAY, "over_budget"),
    }
    verdict = out[0].payload
    assert verdict.status is VerdictStatus.BLOCK
    assert len(verdict.conflicts) == 3


def test_warnings_ride_in_the_block_verdict_but_get_no_conflict_message(seat):
    state = closing_early(demo.demo_state_with_plan())  # unknown fee basis warns too

    out = seat.handle(review_message(state))

    assert sorted(rules_of(out[0].payload.conflicts)) == ["fee_basis_unknown", "open_during_slot"]
    assert [m.payload.rule for m in out if m.type is MessageType.CONFLICT] == ["open_during_slot"]


def test_blocking_conflicts_come_first_in_the_verdict(seat):
    state = closing_early(demo.demo_state_with_plan())
    state = with_item(state, GYM_ID, hours=None)

    rules = rules_of(seat.handle(review_message(state))[0].payload.conflicts)

    assert rules[0] == "open_during_slot"
    assert set(rules[1:]) == {"unknown_hours", "fee_basis_unknown"}


def test_an_anchor_conflict_goes_to_the_concierge(seat):
    state = with_leg(clean_plan(), "stay", "C", minutes_by_mode={Mode.TRANSIT: 70.0}, chosen_mode=Mode.TRANSIT)

    out = seat.handle(review_message(state))

    conflict_messages = [m for m in out if m.type is MessageType.CONFLICT]
    assert [m.to for m in conflict_messages] == [(Seat.CONCIERGE,)]
    assert conflict_messages[0].payload.item_id == "C"


def test_a_revised_plan_clears_after_the_owner_replaces_the_item(seat):
    blocked = closing_early(clean_plan())
    revised = with_item(blocked, REST_ID, hours={"Tue": "11:00-22:00"})

    assert seat.handle(review_message(blocked))[0].payload.status is VerdictStatus.BLOCK
    assert seat.handle(review_message(revised))[0].payload.status is VerdictStatus.CLEAR


def test_the_fee_reserve_setting_decides_the_budget_verdict():
    state = demo.demo_state_with_plan(stay_total=900.0, stay_basis=CostBasis.CARD_TOTAL_FEES_UNKNOWN)

    strict = VerifierSeat(fee_reserve_pct=0.15).handle(review_message(state))[0].payload
    relaxed = VerifierSeat(fee_reserve_pct=0.05).handle(review_message(state))[0].payload

    assert strict.status is VerdictStatus.BLOCK
    assert relaxed.status is VerdictStatus.WARN


def test_the_summary_is_one_short_line_even_with_many_blockers(seat):
    state = clean_plan()
    state = with_item(state, REST_ID, hours={"Tue": "11:00-12:00"})
    state = with_item(state, GYM_ID, hours={"Tue": "06:00-07:00"})
    state = state.bump(stay=state.stay.model_copy(update={"total_price": 5000.0}))

    for message in seat.handle(review_message(state)):
        assert 0 < len(message.summary) <= 300
        assert "\n" not in message.summary


def test_every_outgoing_message_round_trips_through_the_parser(seat):
    out = seat.handle(review_message(closing_early(demo.demo_state_with_plan())))

    assert len(out) >= 2
    for message in out:
        assert parse_message(serialize(message)) == message


# --- messages that need no verdict -------------------------------------------------------------------


def test_a_specialist_proposal_needs_no_answer(seat):
    proposal = parse_message(json.dumps(valid_examples()["proposal"]))

    assert proposal.payload.agent is Seat.RESTAURANT
    assert seat.handle(proposal) == []


def test_a_specialist_proposal_with_a_state_attached_is_still_not_a_plan_to_review(seat):
    incoming = build_message(
        type=MessageType.PROPOSAL,
        sender=Seat.RESTAURANT,
        to=[Seat.CONCIERGE, Seat.VERIFIER],
        trip_id=demo.TRIP_ID,
        user_id=demo.USER_ID,
        state_version=1,
        summary="Dinner option",
        payload=ProposalPayload(agent=Seat.RESTAURANT, items=(demo.demo_restaurant_item(),), state=clean_plan()),
    )

    assert seat.handle(incoming) == []


# --- messages it should never get --------------------------------------------------------------------


@pytest.mark.parametrize("example", ["request", "workout_slot", "preference_learned", "route_reply", "stay_shortlist"])
def test_any_other_type_gets_an_unexpected_type_error(seat, example):
    incoming = parse_message(json.dumps(valid_examples()[example]))

    out = seat.handle(incoming)

    assert len(out) == 1
    error = out[0]
    assert error.type is MessageType.ERROR
    assert error.sender is Seat.VERIFIER
    assert error.to == (incoming.sender,)
    assert error.reply_to == incoming.message_id
    assert isinstance(error.payload, ErrorPayload)
    assert error.payload.code == "unexpected_type"
    assert error.payload.bad_message_id == incoming.message_id
    assert (error.trip_id, error.user_id, error.state_version) == (incoming.trip_id, incoming.user_id, incoming.state_version)


def test_an_incoming_error_is_never_answered_with_an_error(seat):
    """Answering an error with an error lets two seats ping-pong forever; the adapter ends the turn with band_no_reply."""
    incoming = parse_message(json.dumps(valid_examples()["error"]))
    incoming = incoming.model_copy(update={"to": (Seat.VERIFIER,)})

    assert seat.handle(incoming) == []


def test_a_concierge_proposal_without_a_plan_gets_a_missing_state_error(seat):
    incoming = build_message(
        type=MessageType.PROPOSAL,
        sender=Seat.CONCIERGE,
        to=[Seat.VERIFIER],
        trip_id=demo.TRIP_ID,
        user_id=demo.USER_ID,
        state_version=3,
        summary="Merged plan for review",
        payload=ProposalPayload(agent=Seat.CONCIERGE),
    )

    out = seat.handle(incoming)

    assert len(out) == 1
    assert out[0].type is MessageType.ERROR
    assert out[0].to == (Seat.CONCIERGE,)
    assert out[0].payload.code == "missing_state"
    assert out[0].payload.fields == ("payload.state",)
    assert out[0].payload.bad_message_id == incoming.message_id


def test_the_concierge_plan_for_review_example_is_reviewed(seat):
    incoming = parse_message(json.dumps(valid_examples()["plan_for_review"]))

    out = seat.handle(incoming)

    assert out[0].type is MessageType.VERDICT
    assert out[0].payload.status is VerdictStatus.WARN  # the example plan has an unknown fee basis
    assert out[0].payload.plan_hash == demo.demo_state_with_plan().plan_hash()


def test_a_message_the_verifier_sent_itself_is_ignored(seat):
    own_verdict = seat.handle(review_message(clean_plan()))[0]

    assert own_verdict.sender is Seat.VERIFIER
    assert seat.handle(own_verdict) == []
