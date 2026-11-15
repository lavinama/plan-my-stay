"""The Concierge talks only to the Stay agent: one request out, one shortlist back, the top three to the person.

No Routing, no Verifier, no Gym, no Restaurant: the Concierge no longer geocodes, plans trips or checks a plan.
"""

import math

from plan_my_stay import demo
from plan_my_stay.enums import MessageType as M
from plan_my_stay.enums import Seat
from plan_my_stay.messages import Message, build_message
from plan_my_stay.messages.payloads import PreferenceLearnedPayload
from plan_my_stay.models import CostBasis
from plan_my_stay.seat import TextReply
from tests.concierge.fakes import FakeMemory
from tests.concierge.players import (
    ADDRESS_FRAGMENTS,
    PERSON,
    Recorder,
    error_message,
    make_seat,
    messages,
    replies,
    route_reply,
    stay,
    stay_shortlist,
    verdict,
)


def _start(memory=None, llm=None, text="plan the demo trip", **kwargs):
    rec = Recorder(make_seat(memory, llm, stay_only=True, **kwargs))
    out = rec.say(text)
    (request,) = messages(out, M.REQUEST)
    return rec, request, out


def _stays(*commutes, price=600.0):
    return [stay(i, price + i, average_commute_minutes=c) for i, c in enumerate(commutes, start=1)]


def test_the_stay_agent_gets_the_only_request_and_the_person_a_planning_note():
    rec, request, out = _start()

    machine = [m for m in out if isinstance(m, Message)]
    assert machine == [request] and request.to == (Seat.STAY,) and request.type is M.REQUEST
    (note,) = replies(out)
    assert note.mentions == (PERSON,) and "Planning your trip to New York" in note.text


def test_the_request_carries_the_meetings_the_budget_and_a_roster_of_one():
    _, request, _ = _start()
    payload = request.payload

    assert payload.roster == (Seat.STAY,) and payload.response_type == "stay_shortlist"
    assert payload.budget.amount == 1000 and payload.anchors == () and payload.slots == ()
    assert [m.id for m in payload.meetings] == ["A", "B", "C"]
    assert [m.counts_for_stay for m in payload.meetings] == [False, True, True]  # A starts before check-in
    assert {m.address for m in payload.meetings} == set(demo.MEETING_ADDRESSES.values())
    assert payload.zones == demo.demo_zones()


def test_the_run_only_ever_talks_to_the_stay_agent():
    rec, request, _ = _start()
    rec.feed(stay_shortlist(request, *_stays(11.5, 12.5, 13.0)))

    machine = [m for m in rec.sent if isinstance(m, Message)]
    assert {seat for m in machine for seat in m.to} == {Seat.STAY}


def test_the_person_gets_the_stay_agents_first_three_in_its_order():
    rec, request, _ = _start()

    out = rec.feed(stay_shortlist(request, *_stays(11.5, 12.5, 13.0, 20.0)))

    assert [type(item) for item in out] == [TextReply]  # nothing more is sent: the run is over
    text = out[0].text
    assert out[0].mentions == (PERSON,) and "Top 3 stays" in text
    assert text.index("Quiet stay 1") < text.index("Quiet stay 2") < text.index("Quiet stay 3")
    assert "Quiet stay 4" not in text
    assert "Average commute 11.5 min" in text and "Recommended" in text.split("2. Quiet stay 2")[0]


def test_a_stay_over_budget_once_the_fee_reserve_is_added_is_set_aside_and_said_so():
    rec, request, _ = _start()
    over = stay(1, 900, CostBasis.CARD_TOTAL_FEES_UNKNOWN, stay_id="stay-over", name="Overpriced", average_commute_minutes=5.0)
    others = [s.model_copy(update={"rank": s.rank + 1}) for s in _stays(11.5, 12.5, 13.0)]

    text = replies(rec.feed(stay_shortlist(request, over, *others)))[0].text

    assert "Overpriced ranked first but would cost $1,035.00 with a 15% fee reserve" in text and "set aside" in text
    assert text.count("Overpriced") == 1  # named in the note only, not in the ranking
    assert "1. Quiet stay 1" in text


def test_a_stay_with_unconfirmed_fees_shows_the_total_with_the_reserve():
    rec, request, _ = _start()
    unconfirmed = stay(1, 640, CostBasis.CARD_TOTAL_FEES_UNKNOWN, average_commute_minutes=11.5)

    text = replies(rec.feed(stay_shortlist(request, unconfirmed)))[0].text

    assert "Best stay" in text and "fees not confirmed, about $736.00 with a 15% reserve" in text


def test_when_nothing_fits_the_stay_agent_is_asked_for_cheaper_stays_with_the_meetings_again():
    rec, request, _ = _start()

    out = rec.feed(stay_shortlist(request, stay(1, 1100, average_commute_minutes=9.0)))

    (again,) = messages(out, M.REQUEST, Seat.STAY)
    assert again.payload.round == 1 and again.payload.budget.amount == math.floor(1000 / 1.15)
    assert [m.id for m in again.payload.meetings] == ["A", "B", "C"]
    assert replies(out) == []  # still working: nothing for the person yet


def test_after_two_tries_the_budget_is_widened_on_screen_and_the_reply_says_so():
    rec, request, _ = _start()
    rec.feed(stay_shortlist(request))
    rec.feed(stay_shortlist(request))
    out = rec.feed(stay_shortlist(request))
    (widened,) = messages(out, M.REQUEST, Seat.STAY)
    assert widened.payload.budget.amount == 1250 and [m.id for m in widened.payload.meetings] == ["A", "B", "C"]

    out = rec.feed(stay_shortlist(request, stay(1, 1100, average_commute_minutes=9.0)))

    text = replies(out)[0].text
    assert "the budget was widened to $1,250.00" in text and "Best stay" in text


def test_when_nothing_fits_even_after_widening_the_person_is_told_and_the_run_ends():
    rec, request, _ = _start()
    for _ in range(3):
        rec.feed(stay_shortlist(request))

    (note,) = rec.feed(stay_shortlist(request))

    assert "could not find a stay" in note.text and "$1,250.00" in note.text


def test_a_stay_agent_that_keeps_rejecting_the_request_is_given_up_on_and_the_person_told():
    rec, request, _ = _start()
    current = request

    for _ in range(2):  # two resends are allowed
        (current,) = messages(rec.feed(error_message(request, Seat.STAY, current.message_id)), M.REQUEST, Seat.STAY)
    out = rec.feed(error_message(request, Seat.STAY, current.message_id))

    (note,) = out
    assert "Stay agent keeps rejecting" in note.text


def test_a_preference_the_stay_agent_learns_is_written_to_memory():
    memory = FakeMemory()
    rec, request, _ = _start(memory)
    learned = build_message(
        type=M.PREFERENCE_LEARNED, sender=Seat.STAY, to=[Seat.CONCIERGE], trip_id=request.trip_id, user_id=request.user_id,
        state_version=request.state_version, summary="Learned a preference",
        payload=PreferenceLearnedPayload(key="stay_noise", value="quiet", evidence="Dropped a listing on a busy avenue"),
    )

    assert rec.feed(learned) == []

    assert [(p["key"], p["value"]) for p in memory.preferences] == [("stay_noise", "quiet")]


def test_the_trip_state_copy_in_memory_has_no_meeting_address():
    memory = FakeMemory()
    rec, request, _ = _start(memory)
    rec.feed(stay_shortlist(request, *_stays(11.5, 12.5, 13.0)))

    assert memory.saved_states  # a copy was kept
    for state in memory.saved_states:
        body = serialize_state(state)
        assert not any(fragment in body for fragment in ADDRESS_FRAGMENTS)
    assert memory.saved_states[-1].ranked_stays[0].stay_id == "stay-1"


def serialize_state(state) -> str:
    return state.model_dump_json()


def test_the_ranking_is_logged_as_a_trace():
    memory = FakeMemory()
    rec, request, _ = _start(memory)
    rec.feed(stay_shortlist(request, *_stays(11.5, 12.5, 13.0)))

    (trace,) = [t for t in memory.traces if t["step"] == "ranked_stays"]
    assert "stay-1" in trace["outcome"] and trace["agent"] is Seat.CONCIERGE


def test_memory_being_down_does_not_stop_the_answer():
    rec, request, _ = _start(FakeMemory(down=True))

    text = replies(rec.feed(stay_shortlist(request, *_stays(11.5, 12.5, 13.0))))[0].text

    assert "Top 3 stays" in text


def test_messages_meant_for_the_full_pipeline_are_ignored():
    rec, request, _ = _start()

    assert rec.feed(route_reply(request)) == []
    assert rec.feed(verdict(request, __import__("plan_my_stay.enums", fromlist=["VerdictStatus"]).VerdictStatus.CLEAR, "0" * 64)) == []
    assert rec.feed(stay_shortlist(request, *_stays(11.5))) != []  # and the run still finishes normally


def test_after_the_answer_a_comment_is_taken_as_feedback_and_written_to_memory():
    memory = FakeMemory()
    rec, request, _ = _start(memory)
    rec.feed(stay_shortlist(request, *_stays(11.5, 12.5, 13.0)))

    out = rec.say("great, I will book the first one")

    assert "accepted" in replies(out)[0].text and memory.preferences[-1]["key"] == "trip_feedback"


def test_the_full_pipeline_is_still_there_behind_the_flag():
    full = Recorder(make_seat(stay_only=False))

    out = full.say("plan the demo trip")

    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING) and messages(out, M.REQUEST) == []  # it geocodes first
