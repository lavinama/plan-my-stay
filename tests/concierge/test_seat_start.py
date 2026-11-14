"""The Concierge starting a run: intake, the geocode round trip, the requests and collecting the answers."""

import json

from plan_my_stay import demo
from plan_my_stay.concierge.llm import FakeLLM
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.models import Profile
from plan_my_stay.seat import TextSeat
from tests.concierge.fakes import FakeMemory
from tests.concierge.players import (
    PERSON,
    Flow,
    failed_route_reply,
    geocode_reply,
    make_seat,
    messages,
    proposal,
    replies,
    revised_dinner,
    stay,
    stay_pick,
    stay_shortlist,
    workout_slot,
)

M = MessageType


def test_the_seat_is_a_text_seat():
    assert isinstance(make_seat(), TextSeat)


# --- starting a run and the geocode round trip --------------------------------------------------


def test_a_request_asks_routing_to_locate_each_meeting_and_answers_with_a_one_line_plan():
    memory = FakeMemory()
    flow = Flow(make_seat(memory))

    lookups = messages(flow.start_out, M.ROUTE_REQUEST, Seat.ROUTING)
    assert len(lookups) == 3
    for lookup in lookups:
        assert lookup.sender is Seat.CONCIERGE and lookup.to == (Seat.ROUTING,)
        assert lookup.payload.modes == ()  # geocode only
        assert lookup.payload.origin == lookup.payload.destination
    assert {lookup.payload.origin.address for lookup in lookups} == set(demo.MEETING_ADDRESSES.values())
    assert {lookup.payload.depart_at for lookup in lookups} == {a.start for a in demo.demo_anchors()}
    (plan,) = replies(flow.start_out)
    assert plan.mentions == (PERSON,) and "\n" not in plan.text and "New York" in plan.text
    assert memory.profile_loads == [PERSON]


def test_a_text_that_is_not_a_trip_request_falls_back_to_the_demo_and_says_so():
    flow = Flow(make_seat(), "a trip to Boston next week")

    (plan,) = replies(flow.start_out)
    assert "demo" in plan.text.lower() and "could not read" in plan.text
    assert len(messages(flow.start_out, M.ROUTE_REQUEST)) == 3


def test_blank_text_asks_for_the_trip_details_and_starts_nothing():
    out = make_seat().handle_text(PERSON, "   ")

    assert len(out) == 1 and out[0].mentions == (PERSON,) and "budget" in out[0].text


def test_the_llm_reads_a_real_request():
    reading = {
        "city": "Boston, MA",
        "check_in": "2026-12-01",
        "check_out": "2026-12-03",
        "budget_amount": 800,
        "meetings": [{"address": "1 Beacon St, Boston, MA", "date": "2026-12-01", "start": "16:00", "end": "17:00"}],
    }
    flow = Flow(
        make_seat(llm=FakeLLM([json.dumps(reading)])), "Boston Dec 1 to 3, $800, one meeting at 1 Beacon St at 4pm"
    )

    (lookup,) = messages(flow.start_out, M.ROUTE_REQUEST)
    assert lookup.payload.origin.address == "1 Beacon St, Boston, MA"
    assert "Boston" in replies(flow.start_out)[0].text


def test_each_geocode_reply_fills_an_anchor_and_the_last_one_sends_the_requests():
    flow = Flow(make_seat())
    lookups = messages(flow.start_out, M.ROUTE_REQUEST)

    assert flow.rec.feed(geocode_reply(lookups[0])) == []
    assert flow.rec.feed(geocode_reply(lookups[1])) == []
    out = flow.rec.feed(geocode_reply(lookups[2]))

    requests = messages(out, M.REQUEST)
    assert {r.to[0] for r in requests} == {Seat.STAY, Seat.RESTAURANT, Seat.GYM}


def test_the_requests_carry_anchors_zones_slots_roster_and_only_the_profile_each_seat_needs():
    flow = Flow(make_seat(FakeMemory(profile=demo.demo_profile())))
    flow.geocode()
    by_seat = {r.to[0]: r for r in messages(flow.requests_out, M.REQUEST)}

    stay_request, restaurant, gym = (by_seat[s].payload for s in (Seat.STAY, Seat.RESTAURANT, Seat.GYM))
    assert [(a.id, a.lat, a.lng, a.counts_for_stay) for a in stay_request.anchors] == [
        (a.id, a.lat, a.lng, a.counts_for_stay) for a in demo.demo_anchors()
    ]  # A (11:00, before the 15:00 check-in) does not count
    assert stay_request.zones == demo.demo_zones() and stay_request.roster == (Seat.STAY, Seat.RESTAURANT, Seat.GYM)
    assert (stay_request.budget.amount, stay_request.response_type, stay_request.round) == (1000, "stay_shortlist", 0)
    assert restaurant.budget is None and gym.budget is None and restaurant.response_type == "proposal"
    assert {s.kind for s in restaurant.slots} == {"lunch", "dinner"} and {s.kind for s in gym.slots} == {"workout"}
    assert stay_request.slots == ()
    assert restaurant.profile.diet == ("vegetarian",) and restaurant.profile.stay_preferences == ()
    assert gym.profile.gym_types == ("hiit",) and gym.profile.diet == ()
    assert stay_request.profile.stay_preferences == ("quiet street", "private bathroom")
    for request in by_seat.values():
        assert request.state_version == 0 and request.trip_id == flow.trip.trip_id and request.user_id == PERSON


def test_a_new_user_trying_the_demo_gets_the_demo_travelers_preferences():
    flow = Flow(make_seat(FakeMemory()))  # memory knows nothing about this user
    flow.geocode()
    restaurant = next(r for r in messages(flow.requests_out, M.REQUEST) if r.to[0] is Seat.RESTAURANT)

    assert restaurant.payload.profile.diet == ("vegetarian",)


def test_a_remembered_profile_wins_over_the_demo_profile():
    flow = Flow(make_seat(FakeMemory(profile=Profile(diet=("vegan",)))))
    flow.geocode()
    restaurant = next(r for r in messages(flow.requests_out, M.REQUEST) if r.to[0] is Seat.RESTAURANT)

    assert restaurant.payload.profile.diet == ("vegan",)


def test_a_meeting_after_check_in_time_counts_and_an_earlier_one_does_not():
    flow = Flow(make_seat(check_in_time="10:00"))  # A starts at 11:00, after a 10:00 check-in
    flow.geocode()
    request = messages(flow.requests_out, M.REQUEST)[0].payload

    assert [a.counts_for_stay for a in request.anchors] == [True, True, True]


def test_a_copy_of_the_trip_state_is_saved_to_memory_when_the_requests_go_out():
    memory = FakeMemory()
    flow = Flow(make_seat(memory))
    flow.geocode()

    assert len(memory.saved_states) == 1 and memory.saved_states[0].trip_id == flow.trip.trip_id


def test_a_meeting_that_cannot_be_located_ends_the_run_with_a_message():
    flow = Flow(make_seat())
    lookups = messages(flow.start_out, M.ROUTE_REQUEST)

    out = flow.rec.feed(failed_route_reply(lookups[0], "geocode_failed"))

    (note,) = out
    assert note.mentions == (PERSON,) and "meeting A" in note.text and "geocode_failed" in note.text
    assert flow.rec.feed(geocode_reply(lookups[1])) == []  # the run is over


def test_the_roster_has_no_gym_when_the_user_skips_workouts():
    flow = Flow(make_seat(), "demo trip, no workouts")
    flow.geocode()
    requests = messages(flow.requests_out, M.REQUEST)

    assert {r.to[0] for r in requests} == {Seat.STAY, Seat.RESTAURANT}
    assert all(r.payload.roster == (Seat.STAY, Seat.RESTAURANT) for r in requests)
    out = flow.answer_specialists(gym_answers=False)  # the plan does not wait for a gym that was never asked
    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING)
    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)
    assert {e.agent for e in sent.payload.state.entries} == {Seat.RESTAURANT}
    assert sent.payload.state.roster == (Seat.STAY, Seat.RESTAURANT)


# --- collecting ---------------------------------------------------------------------------------


def test_the_run_waits_until_the_shortlist_and_every_proposal_are_in():
    flow = Flow(make_seat())
    flow.geocode()
    trip = flow.trip

    assert flow.rec.feed(stay_shortlist(trip, stay())) == []
    assert flow.rec.feed(proposal(trip, Seat.RESTAURANT, demo.demo_restaurant_item())) == []
    assert flow.rec.feed(workout_slot(trip)) == [] and flow.rec.feed(stay_pick(trip)) == []
    out = flow.rec.feed(proposal(trip, Seat.GYM, demo.demo_gym_item()))
    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING)


def test_the_order_the_answers_arrive_in_does_not_matter():
    flow = Flow(make_seat())
    flow.geocode()
    trip = flow.trip

    assert flow.rec.feed(proposal(trip, Seat.GYM, demo.demo_gym_item())) == []
    assert flow.rec.feed(proposal(trip, Seat.RESTAURANT, demo.demo_restaurant_item())) == []
    assert messages(flow.rec.feed(stay_shortlist(trip, stay())), M.ROUTE_REQUEST)


def test_the_latest_proposal_from_a_seat_replaces_its_earlier_one():
    flow = Flow(make_seat())
    flow.geocode()
    trip = flow.trip
    flow.rec.feed(proposal(trip, Seat.RESTAURANT, demo.demo_restaurant_item()))
    flow.rec.feed(proposal(trip, Seat.RESTAURANT, revised_dinner()))
    flow.rec.feed(stay_shortlist(trip, stay()))
    out = flow.rec.feed(proposal(trip, Seat.GYM, demo.demo_gym_item()))
    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)

    assert [e.item.item_id for e in sent.payload.state.entries if e.agent is Seat.RESTAURANT] == ["rest-late-dosa"]


def test_the_first_item_per_slot_is_kept_because_the_specialist_ranked_it():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists(
        restaurant=[demo.demo_restaurant_item(), revised_dinner().model_copy(update={"slot_id": "s1"})]
    )
    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)

    assert [e.item.item_id for e in sent.payload.state.entries if e.agent is Seat.RESTAURANT] == [
        "rest-urban-vegan-kitchen"
    ]
