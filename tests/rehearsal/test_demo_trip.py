"""The whole swarm on fixtures: the New York demo trip, end to end, through an in-process room.

This is the rehearsal docs/PLAN.md asks for ("a real run beats a polished demo"), runnable with no keys.
Real seats: Concierge, Routing (fixture maps), Verifier, memory (in-memory). Stand-ins: Stay, Restaurant, Gym.
"""

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.memory.in_memory import InMemoryMemory
from plan_my_stay.messages import serialize
from plan_my_stay.rehearsal.room import Room
from plan_my_stay.rehearsal.wiring import build_rehearsal_seats


def _run(memory: InMemoryMemory | None = None, *, text: str = "Plan my demo trip", **seat_options):
    memory = memory if memory is not None else InMemoryMemory()
    room = Room(build_rehearsal_seats(memory, **seat_options))
    room.say("Mario", text)
    return room, memory


def _of_type(room: Room, type_: MessageType):
    return [m for m in room.messages if m.type is type_]


@pytest.fixture(scope="module")
def demo_run():
    return _run()


def test_the_person_gets_a_planning_note_then_the_itinerary(demo_run):
    room, _ = demo_run

    assert len(room.replies) >= 2
    assert all(reply.mentions == ("Mario",) for reply in room.replies)


def test_the_itinerary_shows_the_stay_the_workout_and_the_replacement_dinner(demo_run):
    room, _ = demo_run
    itinerary = room.replies[-1].text

    assert "Quiet Chelsea studio" in itinerary and "640" in itinerary
    assert "Example HIIT Studio" in itinerary and "17:30" in itinerary
    assert "Urban Vegan Kitchen" in itinerary
    assert "Early Closer Cafe" not in itinerary


def test_the_verifier_blocks_the_early_closing_restaurant_once_then_clears_with_a_fee_warning(demo_run):
    room, _ = demo_run

    verdicts = _of_type(room, MessageType.VERDICT)
    assert [v.payload.status.value for v in verdicts] == ["block", "warn"]
    conflicts = _of_type(room, MessageType.CONFLICT)
    assert len(conflicts) == 1
    assert conflicts[0].to == (Seat.RESTAURANT,) and conflicts[0].payload.rule == "open_during_slot"
    assert "fee" in room.replies[-1].text.lower()  # the warning is shown to the user, not swallowed


def test_the_delivered_plan_is_exactly_the_plan_the_verifier_checked(demo_run):
    room, _ = demo_run
    plans = [m for m in _of_type(room, MessageType.PROPOSAL) if m.payload.agent is Seat.CONCIERGE]
    final_verdict = _of_type(room, MessageType.VERDICT)[-1]

    assert len(plans) == 2  # the blocked plan, then the revised one
    assert plans[-1].payload.state.plan_hash() == final_verdict.payload.plan_hash
    assert plans[0].payload.state.plan_hash() != plans[-1].payload.state.plan_hash()


def test_the_trip_state_version_only_goes_up(demo_run):
    room, _ = demo_run
    versions = [m.state_version for m in room.messages if m.sender is Seat.CONCIERGE]

    assert versions == sorted(versions)


def test_specialists_talk_to_each_other_without_going_through_the_concierge(demo_run):
    room, _ = demo_run

    zone_fits = _of_type(room, MessageType.ZONE_FIT)
    assert {m.sender for m in zone_fits} == {Seat.RESTAURANT, Seat.GYM} and all(m.to == (Seat.STAY,) for m in zone_fits)
    assert any(m.to == (Seat.RESTAURANT, Seat.CONCIERGE) or set(m.to) == {Seat.RESTAURANT, Seat.CONCIERGE} for m in _of_type(room, MessageType.WORKOUT_SLOT))
    assert any(Seat.GYM in m.to for m in _of_type(room, MessageType.STAY_PICK))


def test_meeting_addresses_never_leave_the_concierge_except_to_routing(demo_run):
    room, _ = demo_run
    fragments = ("Water St", "9th Ave", "Broadway")

    for message in room.messages:
        if message.sender is Seat.ROUTING or Seat.ROUTING in message.to:
            continue  # Routing is plain code: it has to read an address to geocode it
        body = serialize(message)
        assert not any(f in body for f in fragments), f"{message.type.value} from {message.sender.value} leaks an address"


def test_a_good_first_pick_is_never_blocked():
    room, _ = _run(first_pick_closes_early=False)

    assert _of_type(room, MessageType.CONFLICT) == []
    assert [v.payload.status.value for v in _of_type(room, MessageType.VERDICT)] == ["warn"]
    assert "Urban Vegan Kitchen" in room.replies[-1].text


def test_a_preference_learned_in_run_one_is_remembered_and_run_two_starts_from_it():
    room1, memory = _run()
    user_id = _of_type(room1, MessageType.REQUEST)[0].user_id
    assert memory.load_profile(user_id).model_extra.get("meal_timing") == "early"

    room2, _ = _run(memory, text="Plan my demo trip again")

    second_requests = _of_type(room2, MessageType.REQUEST)
    assert second_requests and all(r.payload.profile.model_extra.get("meal_timing") == "early" for r in second_requests)
    assert second_requests[0].user_id == user_id


def test_another_traveler_does_not_inherit_the_first_travelers_preferences():
    _, memory = _run()

    assert memory.load_profile("someone-else").model_extra.get("meal_timing") is None


def test_a_teammates_real_agent_can_replace_a_stub_in_the_same_rehearsal():
    class QuietGym:
        """Stands in for Prathik's real Gym agent: it answers a request with nothing but its workout slot."""

        def __init__(self):
            self.seen = []

        def handle(self, message):
            self.seen.append(message.type)
            return []

    gym = QuietGym()
    seats = build_rehearsal_seats(InMemoryMemory(), overrides={Seat.GYM: gym})

    assert seats[Seat.GYM] is gym
    assert set(seats) == set(Seat)


def test_the_demo_trip_uses_the_demo_dates_and_budget(demo_run):
    room, _ = demo_run
    request = _of_type(room, MessageType.REQUEST)[0].payload

    assert (request.dates.check_in, request.dates.check_out) == (demo.demo_state().check_in, demo.demo_state().check_out)
    stay_request = next(m for m in _of_type(room, MessageType.REQUEST) if Seat.STAY in m.to)
    assert stay_request.payload.budget.amount == 1000
