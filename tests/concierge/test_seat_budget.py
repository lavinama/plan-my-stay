"""The Concierge's budget gate: cheaper requests, widening, a skipped top stay and arbitration."""

import json

from plan_my_stay import demo
from plan_my_stay.concierge.llm import FakeLLM, LLMError
from plan_my_stay.enums import MessageType, Mode, Seat, VerdictStatus
from plan_my_stay.models import CostBasis
from tests.concierge.players import (
    PERSON,
    Flow,
    make_seat,
    messages,
    proposal,
    replies,
    stay,
    stay_shortlist,
    verdict,
)

M = MessageType


# --- the budget gate ----------------------------------------------------------------------------


def test_a_stay_that_fits_goes_straight_to_routing_for_the_legs():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists(shortlist=[stay(1, 640)])

    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING) and not messages(out, M.REQUEST)


def test_a_top_stay_over_budget_with_the_fee_reserve_gets_a_cheaper_request_to_stay():
    flow = Flow(make_seat())
    flow.geocode()
    flow.answer_specialists(shortlist=[stay(1, 900, CostBasis.CARD_TOTAL_FEES_UNKNOWN)])  # 900 + 15% = 1035 > 1000
    shortlist_message = flow.rec.received[-3]

    out = flow.rec.feed(proposal(flow.trip, Seat.GYM, demo.demo_gym_item()))  # a duplicate changes nothing
    assert out == []
    # The cheaper request went out when the last proposal completed the set: find it in everything sent.
    (cheaper,) = [m for m in messages(flow.rec.sent, M.REQUEST) if m.payload.round == 1]
    assert cheaper.to == (Seat.STAY,) and cheaper.payload.response_type == "stay_shortlist"
    assert cheaper.payload.budget.amount == 869  # 1000 / 1.15: fits even with the reserve
    assert cheaper.reply_to == shortlist_message.message_id
    assert not messages(flow.rec.sent, M.ROUTE_REQUEST, Seat.ROUTING)[3:]  # no legs asked for the over-budget stay


def test_the_cheaper_answer_goes_on_to_routing():
    flow = Flow(make_seat())
    flow.geocode()
    flow.answer_specialists(shortlist=[stay(1, 900, CostBasis.CARD_TOTAL_FEES_UNKNOWN)])

    out = flow.rec.feed(
        stay_shortlist(flow.trip, stay(1, 700, CostBasis.CARD_TOTAL_FEES_UNKNOWN))
    )  # 805 with the reserve

    assert messages(out, M.HOTEL_QUERY)  # the hotel that fits is measured before the legs are asked for
    assert messages(flow.answer_queries(messages(out, M.HOTEL_QUERY)), M.ROUTE_REQUEST, Seat.ROUTING)


def test_a_lower_ranked_stay_that_fits_is_taken_and_the_skipped_one_is_explained():
    flow = Flow(make_seat())
    sent = flow.to_review(shortlist=[stay(1, 900, CostBasis.CARD_TOTAL_FEES_UNKNOWN), stay(2, 950)])

    assert sent.payload.state.stay.stay_id == "stay-2"
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash()))
    assert "ranked first but would cost $1,035.00 with a 15% fee reserve, over your budget" in replies(out)[0].text


def test_nothing_fits_asks_for_cheaper_twice_then_widens_the_budget_and_says_so():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists(shortlist=[])
    (round1,) = messages(out, M.REQUEST)
    assert round1.payload.round == 1 and round1.payload.budget.amount == 869 and replies(out) == []

    out = flow.rec.feed(stay_shortlist(flow.trip))
    (round2,) = messages(out, M.REQUEST)
    assert round2.payload.round == 2 and round2.payload.budget.amount == 869
    assert round2.payload.zones[0].bounds.ne.lat > demo.demo_zones()[0].bounds.ne.lat  # the search area widened too
    assert replies(out) == []

    out = flow.rec.feed(stay_shortlist(flow.trip))  # round 2 found nothing either
    (widened,) = messages(out, M.REQUEST)
    assert widened.payload.round == 2 and widened.payload.budget.amount == 1250
    (notice,) = replies(out)
    assert (
        notice.mentions == (PERSON,)
        and "$1,000.00" in notice.text
        and "$1,250.00" in notice.text
        and "widening" in notice.text
    )


def test_a_stay_found_after_widening_is_checked_against_the_widened_budget_and_flagged_on_the_plan():
    flow = Flow(make_seat())
    flow.geocode()
    flow.answer_specialists(shortlist=[])
    flow.rec.feed(stay_shortlist(flow.trip))
    flow.rec.feed(stay_shortlist(flow.trip))

    out = flow.rec.feed(stay_shortlist(flow.trip, stay(1, 1100)))  # over $1,000 but within the widened $1,250
    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)
    assert sent.payload.state.budget.amount == 1250 and sent.payload.state.stay.total_price == 1100
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash()))
    assert "the budget was widened to $1,250.00" in replies(out)[0].text


def test_when_nothing_fits_even_after_widening_the_person_is_told_and_the_run_ends():
    flow = Flow(make_seat())
    flow.geocode()
    flow.answer_specialists(shortlist=[])
    for _ in range(3):
        out = flow.rec.feed(stay_shortlist(flow.trip))

    (note,) = out
    assert note.mentions == (PERSON,) and "$1,250.00" in note.text and "could not find a stay" in note.text


def test_two_close_stays_are_arbitrated_and_the_trade_off_is_on_the_itinerary():
    def commute(b: float, c: float) -> dict:
        return {"B": {Mode.TRANSIT: b}, "C": {Mode.TRANSIT: c}}

    chelsea = stay(1, 640, name="Quiet Chelsea studio", commute_minutes=commute(11, 15))
    tribeca = stay(2, 650, name="TriBeCa loft", commute_minutes=commute(10, 14))
    trade_off = (
        "The TriBeCa loft costs $650.00 with a 12 min average commute. The Chelsea studio costs $640.00 with 13 min."
    )
    llm = FakeLLM([json.dumps({"choice": "stay-2", "trade_off": trade_off}), LLMError("no polish today")])
    flow = Flow(make_seat(llm=llm))

    sent = flow.to_review(shortlist=[chelsea, tribeca])

    assert sent.payload.state.stay.stay_id == "stay-2"  # the Concierge arbitrated a close call
    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash()))
    assert trade_off in replies(out)[0].text


def test_a_stay_that_is_not_a_close_call_is_not_arbitrated():
    llm = FakeLLM([])
    flow = Flow(make_seat(llm=llm))

    flow.to_review(shortlist=[stay(1, 640), stay(2, 650)])  # no commute data: cannot tell, so no model call

    assert llm.calls == ()
