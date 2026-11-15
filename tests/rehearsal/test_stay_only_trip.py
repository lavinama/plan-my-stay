"""The Concierge and the Stay agent, end to end through the in-process room: the demo trip with nobody else."""

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.memory.in_memory import InMemoryMemory
from plan_my_stay.messages import serialize
from plan_my_stay.rehearsal.room import Room
from plan_my_stay.rehearsal.wiring import build_rehearsal_seats


def _run(memory=None, text="Plan my demo trip"):
    memory = memory if memory is not None else InMemoryMemory()
    room = Room(build_rehearsal_seats(memory))
    room.say("Mario", text)
    return room, memory


def _of_type(room, type_):
    return [m for m in room.messages if m.type is type_]


@pytest.fixture(scope="module")
def trip():
    return _run()


def test_the_person_gets_a_planning_note_then_the_top_three_stays(trip):
    room, _ = trip

    assert len(room.replies) == 2 and all(r.mentions == ("Mario",) for r in room.replies)
    text = room.replies[-1].text
    assert text.startswith("Top 3 stays: New York, NY")
    # the Stay agent's order is by average commute: Chelsea 11.5, TriBeCa 12.5, Financial District 13.0
    assert text.index("Quiet Chelsea studio") < text.index("TriBeCa loft") < text.index("Financial District hotel")
    assert "Financial District penthouse" not in text  # $1,200 is over the budget: the Stay agent drops it


def test_the_stay_agents_commute_order_is_what_the_person_sees(trip):
    room, _ = trip
    shortlist = _of_type(room, MessageType.STAY_SHORTLIST)[0].payload.stays
    text = room.replies[-1].text

    positions = [text.index(s.name) for s in shortlist[:3]]
    assert positions == sorted(positions) and [s.rank for s in shortlist] == list(range(1, len(shortlist) + 1))
    assert "Average commute 11.5 min" in text.split("2. ")[0]  # the best commute first


def test_only_the_concierge_and_the_stay_agent_take_part(trip):
    room, _ = trip

    seats = {m.sender for m in room.messages} | {s for m in room.messages for s in m.to}
    assert seats == {Seat.CONCIERGE, Seat.STAY}
    assert [m.type.value for m in room.messages][:2] == ["request", "stay_shortlist"]


def test_meeting_addresses_go_to_the_stay_agent_in_the_request_and_nowhere_else(trip):
    room, _ = trip
    fragments = ("Water St", "9th Ave", "Broadway")

    for message in room.messages:
        body = serialize(message)
        leaks = any(f in body for f in fragments)
        assert leaks == (message.type is MessageType.REQUEST), f"{message.type.value} from {message.sender.value}"


def test_the_trip_state_copy_kept_in_memory_names_no_meeting_address(trip):
    room, memory = trip
    trip_id = _of_type(room, MessageType.REQUEST)[0].trip_id

    state = memory.load_trip_state(user_id="Mario", trip_id=trip_id)

    assert state is not None and len(state.ranked_stays) == 3
    assert not any(fragment in state.model_dump_json() for fragment in ("Water St", "9th Ave", "Broadway"))


def test_a_preference_the_stay_agent_learned_in_run_one_is_sent_back_to_it_in_run_two():
    room1, memory = _run()
    assert memory.load_profile("Mario").model_extra.get("stay_noise") == "quiet"

    room2, _ = _run(memory, text="Plan my demo trip again")

    first_request = _of_type(room2, MessageType.REQUEST)[0]
    assert first_request.payload.profile.model_extra.get("stay_noise") == "quiet"
    assert first_request.user_id == "Mario"


def test_another_traveler_does_not_inherit_the_first_travelers_preference():
    _, memory = _run()

    assert memory.load_profile("someone-else").model_extra.get("stay_noise") is None


def test_the_demo_trip_uses_the_demo_dates_and_budget(trip):
    room, _ = trip
    request = _of_type(room, MessageType.REQUEST)[0].payload

    assert (request.dates.check_in, request.dates.check_out) == (demo.demo_state().check_in, demo.demo_state().check_out)
    assert request.budget.amount == 1000 and request.roster == (Seat.STAY,)
