"""Memory (best effort), feedback, out-of-phase messages, error replies and what never leaves the Concierge."""

import json
import re

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat, VerdictStatus
from plan_my_stay.messages import serialize
from tests.concierge.fakes import FakeMemory
from tests.concierge.players import (
    ADDRESS_FRAGMENTS,
    PERSON,
    Flow,
    conflict_message,
    deliver,
    error_for,
    foreign_message,
    make_seat,
    messages,
    preference_learned,
    proposal,
    replies,
    route_reply,
    stay,
    stay_shortlist,
    verdict,
    workout_slot,
)

M = MessageType


# --- memory -------------------------------------------------------------------------------------


def test_a_learned_preference_is_written_to_memory_and_nothing_is_sent():
    memory = FakeMemory()
    flow = Flow(make_seat(memory))
    flow.geocode()

    assert flow.rec.feed(preference_learned(flow.trip, "eats_early", True, "asked for 18:00 dinners")) == []

    (saved,) = memory.preferences
    assert saved == {
        "user_id": PERSON,
        "trip_id": flow.trip.trip_id,
        "key": "eats_early",
        "value": True,
        "evidence": "asked for 18:00 dinners",
    }
    assert memory.traces[-1]["step"] == "preference_learned"


def test_memory_being_down_does_not_break_the_run():
    memory = FakeMemory(down=True)
    flow = Flow(make_seat(memory))
    flow.geocode()
    assert flow.rec.feed(preference_learned(flow.trip)) == []
    flow.rec.received.clear()
    out = flow.answer_specialists()
    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash()))

    text = replies(out)[0].text
    assert "Trip plan" in text and "memory" not in text.lower()  # the itinerary says nothing about it
    assert memory.saved_states == [] and memory.preferences == []


def test_feedback_after_delivery_is_written_to_memory_as_plain_text():
    memory = FakeMemory()
    flow = Flow(make_seat(memory))
    deliver(flow)

    out = flow.rec.say("Perfect, I'll book it. Love the quiet street.")
    (thanks,) = out
    assert thanks.mentions == (PERSON,)
    saved = memory.preferences[-1]
    assert (saved["key"], saved["value"]) == ("trip_feedback", "accepted")
    assert "Love the quiet street" in saved["evidence"]
    assert memory.traces[-1]["step"] == "user_feedback"

    flow.rec.say("Actually it is too far from my second meeting, I reject it")
    assert memory.preferences[-1]["value"] == "rejected" and "too far" in memory.preferences[-1]["evidence"]
    flow.rec.say("Is there a pool nearby?")
    assert memory.preferences[-1]["value"] == "commented"


def test_feedback_still_gets_an_answer_when_memory_is_down():
    flow = Flow(make_seat(FakeMemory(down=True)))
    deliver(flow)

    assert len(flow.rec.say("Looks good")) == 1


def test_new_trip_after_delivery_starts_a_fresh_run():
    flow = Flow(make_seat())
    deliver(flow)

    out = flow.rec.say("new trip, demo")

    lookups = messages(out, M.ROUTE_REQUEST)
    assert len(lookups) == 3 and lookups[0].trip_id != flow.trip.trip_id


def test_text_while_planning_does_not_start_a_second_run():
    flow = Flow(make_seat())
    flow.geocode()

    out = flow.rec.say("How is it going?")

    (note,) = out
    assert "still planning" in note.text and not messages(out)


# --- unknown and out-of-phase messages, and errors ----------------------------------------------


def test_messages_for_an_unknown_trip_or_out_of_phase_are_ignored(caplog):
    seat = make_seat()
    flow = Flow(seat)
    stray = stay_shortlist(flow.rec.received[0] if flow.rec.received else foreign_message(), stay())

    assert seat.handle(stray) == []  # no run for that trip
    flow.geocode()
    flow.rec.feed(stay_shortlist(flow.trip, stay()))
    assert (
        seat.handle(route_reply(messages(flow.start_out, M.ROUTE_REQUEST)[0])) == []
    )  # not a lookup we are waiting for
    conflict = conflict_message(flow.trip)
    assert seat.handle(conflict) == []  # conflicts go to the owner, not the Concierge


def test_late_answers_after_delivery_are_ignored():
    flow = Flow(make_seat())
    deliver(flow)

    assert flow.rec.feed(stay_shortlist(flow.trip, stay())) == []
    assert flow.rec.feed(proposal(flow.trip, Seat.RESTAURANT, demo.demo_restaurant_item())) == []
    assert flow.rec.feed(workout_slot(flow.trip)) == []


def test_a_proposal_whose_agent_is_not_its_sender_or_not_in_the_roster_is_ignored():
    flow = Flow(make_seat(), "demo trip, no workouts")
    flow.geocode()

    assert flow.rec.feed(proposal(flow.trip, Seat.GYM, demo.demo_gym_item())) == []  # no gym in this run


def test_a_specialist_that_rejects_its_request_is_asked_twice_more_and_then_dropped():
    flow = Flow(make_seat())
    flow.geocode()
    gym_request = next(r for r in messages(flow.requests_out, M.REQUEST) if r.to[0] is Seat.GYM)
    current = gym_request

    for _ in range(2):
        out = flow.rec.feed(error_for(flow.trip, Seat.GYM, current))
        (again,) = messages(out, M.REQUEST)
        assert again.to == (Seat.GYM,) and again.message_id != current.message_id and again.payload == current.payload
        current = again
    out = flow.rec.feed(error_for(flow.trip, Seat.GYM, current))

    (notice,) = replies(out)
    assert "Gym agent" in notice.text and "without it" in notice.text
    flow.answer_specialists(gym_answers=False)
    (sent,) = messages(flow.answer_legs(flow.rec.sent[-5:]), M.PROPOSAL, Seat.VERIFIER)
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash()))
    assert "Gym agent was unavailable" in replies(out)[0].text


def test_a_stale_error_is_ignored():
    flow = Flow(make_seat())
    flow.geocode()
    request = next(r for r in messages(flow.requests_out, M.REQUEST) if r.to[0] is Seat.GYM)

    assert flow.rec.feed(error_for(flow.trip, Seat.GYM, request)) != []  # the first one is answered with a resend
    assert flow.rec.feed(error_for(flow.trip, Seat.GYM, request)) == []  # the same id again is stale


def test_stay_rejecting_its_request_for_good_ends_the_run():
    flow = Flow(make_seat())
    flow.geocode()
    current = next(r for r in messages(flow.requests_out, M.REQUEST) if r.to[0] is Seat.STAY)

    for _ in range(2):
        (current,) = messages(flow.rec.feed(error_for(flow.trip, Seat.STAY, current)), M.REQUEST)
    out = flow.rec.feed(error_for(flow.trip, Seat.STAY, current))

    (note,) = out
    assert "Stay agent" in note.text and "cannot plan a stay" in note.text


def test_routing_rejecting_a_leg_request_leaves_that_leg_out():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists()
    lookups = messages(out, M.ROUTE_REQUEST, Seat.ROUTING)
    flow.rec.feed(error_for(flow.trip, Seat.ROUTING, lookups[0], "validation_failed"))
    out = flow.rec.feed_all(route_reply(r) for r in lookups[1:])

    (sent,) = messages(out, M.PROPOSAL, Seat.VERIFIER)
    assert len(sent.payload.state.legs) == 4


# --- privacy ------------------------------------------------------------------------------------


def test_no_meeting_address_is_in_any_message_to_a_specialist_or_the_verifier_or_in_a_leg_request():
    memory = FakeMemory()
    flow = Flow(make_seat(memory))
    flow.rec.say("plan the demo trip")  # a second run in the same seat
    sent, _ = deliver(flow)

    wire = [m for m in messages(flow.rec.sent) if not (m.type is M.ROUTE_REQUEST and m.payload.modes == ())]
    assert wire  # requests, leg lookups and the plan for the Verifier
    for message in wire:
        body = serialize(message)
        for fragment in (*demo.MEETING_ADDRESSES.values(), *ADDRESS_FRAGMENTS):
            assert fragment not in body, f"{message.type.value} to {message.to} leaks {fragment!r}"
    # and nothing written to memory carries one either
    stored = json.dumps([s.model_dump(mode="json") for s in memory.saved_states]) + json.dumps(
        memory.traces, default=str
    )
    assert not any(fragment in stored for fragment in ADDRESS_FRAGMENTS)
    assert re.search(r"Water St", serialize(sent)) is None


def test_the_geocode_lookups_to_routing_are_the_only_messages_that_carry_an_address():
    flow = Flow(make_seat())
    flow.to_review()

    carrying = [m for m in messages(flow.rec.sent) if any(f in serialize(m) for f in ADDRESS_FRAGMENTS)]

    assert carrying and all(
        m.type is M.ROUTE_REQUEST and m.to == (Seat.ROUTING,) and m.payload.modes == () for m in carrying
    )
