"""The merged plan, the Verifier's verdicts, the block and revise loop, and the itinerary the person gets."""

import pytest

from plan_my_stay import demo
from plan_my_stay.concierge.llm import FakeLLM, LLMError
from plan_my_stay.enums import MessageType, Mode, Seat, VerdictStatus
from plan_my_stay.models import Conflict
from tests.concierge.fakes import FakeMemory
from tests.concierge.players import (
    DINNER_CONFLICT,
    PERSON,
    Flow,
    deliver,
    failed_route_reply,
    make_seat,
    messages,
    proposal,
    replies,
    revised_dinner,
    route_reply,
    stay,
    stay_pick,
    stay_shortlist,
    verdict,
)

M = MessageType


# --- legs and the merged plan -------------------------------------------------------------------


def test_the_legs_are_asked_with_coordinates_only_and_the_fastest_mode_is_chosen():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists()
    lookups = messages(out, M.ROUTE_REQUEST, Seat.ROUTING)

    assert len(lookups) == 5
    for lookup in lookups:
        assert lookup.payload.origin.address is None and lookup.payload.destination.address is None
        assert lookup.payload.origin.lat is not None and lookup.payload.modes == (
            Mode.WALKING,
            Mode.TRANSIT,
            Mode.DRIVING,
        )
    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)
    legs = sent.payload.state.legs
    assert [(leg.from_id, leg.to_id) for leg in legs] == [
        ("A", "B"),
        ("B", "gym-example-hiit"),
        ("gym-example-hiit", "rest-urban-vegan-kitchen"),
        ("rest-urban-vegan-kitchen", "stay"),
        ("stay", "C"),
    ]
    assert all(leg.chosen_mode is Mode.DRIVING and leg.minutes_by_mode[Mode.DRIVING] == 12 for leg in legs)


def test_the_merged_plan_goes_to_the_verifier_as_a_concierge_proposal_with_the_exact_state():
    flow = Flow(make_seat())
    sent = flow.to_review()
    state = sent.payload.state

    assert sent.sender is Seat.CONCIERGE and sent.to == (Seat.VERIFIER,) and sent.payload.agent is Seat.CONCIERGE
    assert sent.state_version == state.state_version > 0
    assert state.stay.stay_id == "stay-1" and state.stay_shortlist[0].stay_id == "stay-1"
    assert {e.item.item_id for e in state.entries} == {"gym-example-hiit", "rest-urban-vegan-kitchen"}
    assert (state.round, state.budget.amount, state.city) == (0, 1000, "New York, NY")
    assert [a.id for a in state.anchors] == ["A", "B", "C"]


def test_state_versions_only_go_up():
    flow = Flow(make_seat())
    flow.to_review()
    versions = [m.state_version for m in messages(flow.rec.sent) if m.sender is Seat.CONCIERGE]

    assert versions == sorted(versions) and versions[0] == 0 and versions[-1] > 0


def test_a_stay_with_no_coordinates_is_located_by_the_stay_pick_then_by_its_zone():
    flow = Flow(make_seat())
    flow.geocode()
    flow.rec.feed(stay_pick(flow.trip, stay_id="stay-1", lat=40.70, lng=-74.01))
    out = flow.answer_specialists(shortlist=[stay(1, 640, lat=None, lng=None)])
    to_stay = next(m for m in messages(out, M.ROUTE_REQUEST) if m.payload.destination.lat == pytest.approx(40.70))
    assert to_stay.payload.destination.lng == pytest.approx(-74.01)

    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists(
        shortlist=[stay(1, 640, lat=None, lng=None)]
    )  # no pick: the centre of zone z1 (Chelsea)
    assert any(
        m.payload.destination.lat == pytest.approx((40.756 + 40.738) / 2) for m in messages(out, M.ROUTE_REQUEST)
    )


def test_a_leg_routing_cannot_find_is_left_out_and_shown_as_a_warning():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists()
    lookups = messages(out, M.ROUTE_REQUEST, Seat.ROUTING)
    answers = [failed_route_reply(lookups[0], "no_route")] + [route_reply(r) for r in lookups[1:]]
    out = flow.rec.feed_all(answers)

    (sent,) = messages(out, M.PROPOSAL, Seat.VERIFIER)
    assert len(sent.payload.state.legs) == 4
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.WARN, sent.payload.state.plan_hash(), []))
    assert "Travel time from Meeting A to Meeting B could not be found (no_route)" in replies(out)[0].text


# --- the verdict --------------------------------------------------------------------------------


def test_a_clear_verdict_for_the_current_plan_delivers_the_itinerary_and_saves_the_state_and_a_trace():
    memory = FakeMemory()
    flow = Flow(make_seat(memory))
    sent, out = deliver(flow)

    (itinerary,) = out
    assert itinerary.mentions == (PERSON,)
    for address in demo.MEETING_ADDRESSES.values():
        assert address in itinerary.text
    for needle in (
        "$640.00",
        "$35.00",
        "$1,000.00",
        "Quiet stay 1",
        "Urban Vegan Kitchen",
        "Example HIIT Studio",
        "driving 12 min",
    ):
        assert needle in itinerary.text
    for clock in ("11:00-12:00", "16:00-17:00", "17:30-18:15", "18:30-19:45", "10:00-11:00"):
        assert clock in itinerary.text
    assert memory.saved_states[-1].plan_hash() == sent.payload.state.plan_hash()
    assert [t["step"] for t in memory.traces] == ["delivered"] and memory.traces[0]["agent"] is Seat.CONCIERGE


def test_a_warn_verdict_is_delivered_and_every_warning_is_shown():
    conflicts = [
        Conflict(rule="fee_basis_unknown", item_id="stay-1", detail="A 15% fee reserve was added to the stay total."),
        Conflict(rule="unknown_hours", item_id="gym-example-hiit", detail="No opening hours for Tuesday."),
    ]
    _, out = deliver(Flow(make_seat()), VerdictStatus.WARN, conflicts)

    text = replies(out)[0].text
    assert "Heads-up" in text and all(c.detail in text for c in conflicts)


def test_a_verdict_for_a_stale_plan_hash_is_ignored():
    flow = Flow(make_seat())
    sent = flow.to_review()

    assert flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, "0" * 64)) == []
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash()))
    assert len(replies(out)) == 1
    assert flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash())) == []  # a duplicate


def test_a_verdict_before_the_plan_is_with_the_verifier_is_ignored():
    flow = Flow(make_seat())
    flow.geocode()

    assert flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, "a" * 64)) == []


def test_block_then_revise_then_clear():
    flow = Flow(make_seat())
    first = flow.to_review()
    block = verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[DINNER_CONFLICT])

    assert flow.rec.feed(block) == []  # waiting for the restaurant seat
    out = flow.rec.feed(proposal(flow.trip, Seat.RESTAURANT, revised_dinner()))
    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING)  # a changed plan needs new travel times
    (second,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)

    state = second.payload.state
    assert state.round == 1 and second.reply_to == block.message_id
    assert {e.item.item_id for e in state.entries} == {"gym-example-hiit", "rest-late-dosa"}
    assert (
        state.plan_hash() != first.payload.state.plan_hash() and state.state_version > first.payload.state.state_version
    )
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, state.plan_hash()))
    assert "Late Night Dosa" in replies(out)[0].text


def test_the_clear_for_the_old_plan_is_stale_once_the_plan_was_revised():
    flow = Flow(make_seat())
    first = flow.to_review()
    flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[DINNER_CONFLICT]))
    out = flow.rec.feed(proposal(flow.trip, Seat.RESTAURANT, revised_dinner()))
    (second,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)

    assert flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, first.payload.state.plan_hash())) == []
    assert len(replies(flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, second.payload.state.plan_hash())))) == 1


def test_a_late_block_for_an_earlier_plan_is_ignored_by_its_reply_to():
    flow = Flow(make_seat())
    first = flow.to_review()
    flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[DINNER_CONFLICT], reply_to=first.message_id))
    out = flow.rec.feed(proposal(flow.trip, Seat.RESTAURANT, revised_dinner()))
    (second,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)

    late = verdict(
        flow.trip, VerdictStatus.BLOCK, conflicts=[DINNER_CONFLICT], reply_to=first.message_id
    )  # a retry of the first
    assert flow.rec.feed(late) == []
    out = flow.rec.feed(
        verdict(flow.trip, VerdictStatus.CLEAR, second.payload.state.plan_hash(), reply_to=second.message_id)
    )
    assert len(replies(out)) == 1  # the run was not pushed into another revision round


def test_only_the_owner_of_a_conflict_can_revise():
    flow = Flow(make_seat())
    flow.to_review()
    flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[DINNER_CONFLICT]))

    assert flow.rec.feed(proposal(flow.trip, Seat.GYM, demo.demo_gym_item())) == []  # the gym owns nothing in conflict


def test_a_second_block_while_waiting_for_revisions_changes_nothing():
    flow = Flow(make_seat())
    flow.to_review()
    flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[DINNER_CONFLICT]))
    gym_conflict = Conflict(rule="travel_time", item_id="gym-example-hiit", detail="Too little time to reach the gym.")

    assert (
        flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[gym_conflict])) == []
    )  # still waiting for the dinner
    out = flow.rec.feed(proposal(flow.trip, Seat.RESTAURANT, revised_dinner()))
    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING)  # the original owner's revision still moves the plan on


def test_a_block_that_also_carries_warnings_waits_only_for_the_owners_of_the_blocking_conflicts():
    flow = Flow(make_seat())
    flow.to_review()
    fee_warning = Conflict(
        rule="fee_basis_unknown", item_id="stay-1", detail="A fee reserve was added to the stay total."
    )
    flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[DINNER_CONFLICT, fee_warning]))

    out = flow.rec.feed(proposal(flow.trip, Seat.RESTAURANT, revised_dinner()))  # Stay is not asked to revise a warning

    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING)


def test_a_stay_conflict_goes_back_to_stay_which_answers_with_a_new_shortlist():
    flow = Flow(make_seat())
    flow.to_review()
    over = Conflict(
        rule="over_budget", item_id="stay-1", detail="The stay is over budget.", suggestion="Find a cheaper stay."
    )
    flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[over]))

    out = flow.rec.feed(stay_shortlist(flow.trip, stay(1, 500, stay_id="stay-cheap", name="Cheap stay")))
    (second,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)

    assert second.payload.state.stay.stay_id == "stay-cheap" and second.payload.state.round == 1


def test_after_two_rounds_the_plan_is_delivered_with_the_open_conflicts_as_warnings():
    memory = FakeMemory()
    flow = Flow(make_seat(memory))
    sent = flow.to_review()
    dinner_id = "rest-urban-vegan-kitchen"
    for n in (1, 2):
        conflict = DINNER_CONFLICT.model_copy(update={"item_id": dinner_id})
        assert flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[conflict])) == []
        dinner_id = f"rest-revised-{n}"
        out = flow.rec.feed(
            proposal(flow.trip, Seat.RESTAURANT, revised_dinner(item_id=dinner_id, name=f"Revised Dinner {n}"))
        )
        (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)
        assert sent.payload.state.round == n

    stuck = Conflict(rule="open_during_slot", item_id=dinner_id, detail="Still closes at 19:00 on Tue.")
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, conflicts=[stuck]))  # round 2 is the last

    (itinerary,) = out
    assert "Heads-up" in itinerary.text and "Still closes at 19:00 on Tue." in itinerary.text
    assert memory.saved_states[-1].open_conflicts == (stuck,)
    assert (
        flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash())) == []
    )  # the run is over


# --- the model: polish, failure and fallback ----------------------------------------------------


def test_a_faithful_polish_from_the_llm_is_what_the_person_gets():
    def polish(call):
        return "Have a great trip!\n\n" + call.prompt.split("Itinerary:\n\n", 1)[1]

    llm = FakeLLM([polish])
    _, out = deliver(Flow(make_seat(llm=llm)))

    assert replies(out)[0].text.startswith("Have a great trip!")
    assert llm.calls[0].json_schema is None


def test_an_llm_that_fails_at_the_end_falls_back_to_the_template():
    _, out = deliver(Flow(make_seat(llm=FakeLLM([LLMError("model is down")]))))

    assert replies(out)[0].text.startswith("Trip plan: New York, NY")


def test_an_llm_that_adds_a_price_falls_back_to_the_template():
    _, out = deliver(Flow(make_seat(llm=FakeLLM([lambda call: call.prompt + "\nDinner is about $99.00."]))))

    assert "$99.00" not in replies(out)[0].text
