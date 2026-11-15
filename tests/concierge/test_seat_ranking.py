"""The Concierge ranks the hotels from what the Stay, Gym and Restaurant agents returned and builds the plan around
the best one (docs/SEAT_PROTOCOL.md, section 5)."""

from plan_my_stay.concierge.ranking import Weights
from plan_my_stay.enums import MessageType as M
from plan_my_stay.enums import Seat, VerdictStatus
from plan_my_stay.messages import serialize
from tests.concierge.fakes import FakeMemory
from tests.concierge.players import (
    ADDRESS_FRAGMENTS,
    DINNER_CONFLICT,
    Flow,
    error_message,
    gym_fit,
    make_seat,
    messages,
    proposal,
    replies,
    restaurant_fit,
    revised_dinner,
    stay,
    verdict,
)

# Stay agent's order is by average commute: 1 < 2 < 3. Hotel 3 is far better on gym and restaurants, so the
# combined ranking is 3, 2, 1 (scores by hand in the comments of tests/concierge/test_ranking.py).
THREE = [
    stay(1, 640, average_commute_minutes=12.0),
    stay(2, 700, average_commute_minutes=12.5),
    stay(3, 720, average_commute_minutes=13.0),
]
GYMS = {"stay-1": None, "stay-2": 1000.0, "stay-3": 100.0}
FOOD = {"stay-1": None, "stay-2": 6.0, "stay-3": 1.5}


def _planned(flow: Flow, **kwargs):
    """Run to the merged plan and return the proposal the Verifier gets."""
    flow.geocode()
    out = flow.answer_specialists(**kwargs)
    out = flow.answer_legs(out)
    (sent,) = messages(out, M.PROPOSAL, Seat.VERIFIER)
    return sent


def test_after_the_budget_gate_gym_and_restaurant_are_asked_about_each_fitting_hotel():
    flow = Flow(make_seat())
    flow.geocode()

    out = flow.answer_specialists(shortlist=[*THREE, stay(4, 1200)], answer_fits=False)

    (gym_query,) = messages(out, M.HOTEL_QUERY, Seat.GYM)
    (food_query,) = messages(out, M.HOTEL_QUERY, Seat.RESTAURANT)
    for query in (gym_query, food_query):
        assert [h.stay_id for h in query.payload.hotels] == ["stay-1", "stay-2", "stay-3"]  # stay-4 is over budget
        assert query.payload.radius_minutes == 10
        assert all(h.location_precision.value == "exact" for h in query.payload.hotels)
    assert messages(out, M.ROUTE_REQUEST) == []  # the legs wait for the ranking


def test_a_hotel_without_an_address_is_measured_from_the_centre_of_its_zone():
    flow = Flow(make_seat())
    flow.geocode()
    zone_level = stay(1, 640, lat=None, lng=None, zone_id="z2", average_commute_minutes=12.0)

    out = flow.answer_specialists(shortlist=[zone_level], answer_fits=False)

    (point,) = messages(out, M.HOTEL_QUERY, Seat.GYM)[0].payload.hotels
    assert point.location_precision.value == "zone"
    assert abs(point.lat - 40.71935) < 1e-6 and abs(point.lng - -74.0055) < 1e-6  # the middle of TriBeCa's box


def test_a_hotel_query_never_carries_a_meeting_address():
    flow = Flow(make_seat())
    flow.geocode()

    out = flow.answer_specialists(shortlist=THREE, answer_fits=False)

    for query in messages(out, M.HOTEL_QUERY):
        assert not any(fragment in serialize(query) for fragment in ADDRESS_FRAGMENTS)


def test_the_plan_is_built_around_the_hotel_the_concierge_ranks_first_not_the_stay_agents_first():
    flow = Flow(make_seat())

    sent = _planned(flow, shortlist=THREE, gym_fits=GYMS, restaurant_fits=FOOD)

    state = sent.payload.state
    assert state.stay.stay_id == "stay-3"
    assert [r.stay_id for r in state.ranked_stays] == ["stay-3", "stay-2", "stay-1"]
    assert [r.rank for r in state.ranked_stays] == [1, 2, 3]
    best = state.ranked_stays[0]
    assert (best.nearest_gym_m, best.restaurant_average_rank, best.average_commute_minutes) == (100.0, 1.5, 13.0)


def test_only_the_top_three_hotels_are_kept():
    five = [stay(i, 600 + i, average_commute_minutes=10 + i) for i in range(1, 6)]
    flow = Flow(make_seat())

    sent = _planned(flow, shortlist=five)

    assert [r.stay_id for r in sent.payload.state.ranked_stays] == ["stay-1", "stay-2", "stay-3"]


def test_hotels_over_budget_with_the_fee_reserve_are_not_ranked():
    # 900 with an unknown fee basis is 1,035 with the 15% reserve: over the $1,000 budget
    over = stay(1, 900, stay_id="stay-over", basis="card_total_fees_unknown", average_commute_minutes=5.0)
    behind_it = [s.model_copy(update={"rank": s.rank + 1}) for s in THREE]  # the shortlist's ranks run 1..n
    flow = Flow(make_seat())

    sent = _planned(flow, shortlist=[over, *behind_it])

    assert "stay-over" not in [r.stay_id for r in sent.payload.state.ranked_stays]
    assert len(sent.payload.state.ranked_stays) == 3


def test_without_a_gym_seat_only_the_restaurant_is_asked_and_the_ranking_uses_the_other_two_factors():
    flow = Flow(make_seat(), "demo trip, no workouts")
    flow.geocode()

    out = flow.answer_specialists(shortlist=THREE, gym_answers=False, answer_fits=False)

    assert messages(out, M.HOTEL_QUERY, Seat.GYM) == []
    assert len(messages(out, M.HOTEL_QUERY, Seat.RESTAURANT)) == 1
    out = flow.rec.feed(restaurant_fit(messages(out, M.HOTEL_QUERY, Seat.RESTAURANT)[0], FOOD))
    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)
    assert set(sent.payload.state.ranked_stays[0].scores) == {"commute", "restaurants"}


def test_the_ranking_waits_for_both_agents():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists(shortlist=THREE, answer_fits=False)
    gym_query = messages(out, M.HOTEL_QUERY, Seat.GYM)[0]

    assert flow.rec.feed(gym_fit(gym_query, GYMS)) == []  # still waiting for the restaurants


def test_an_answer_outside_the_ranking_step_is_ignored():
    flow = Flow(make_seat())
    sent = _planned(flow, shortlist=THREE, gym_fits=GYMS, restaurant_fits=FOOD)
    late = gym_fit(flow.queries[0], 50.0)

    assert flow.rec.feed(late) == []  # the plan is already with the Verifier
    assert sent.payload.state.ranked_stays[0].nearest_gym_m == 100.0


def test_custom_weights_change_the_winner():
    commute_only = Weights(commute=1, gym=0, restaurants=0)
    flow = Flow(make_seat(weights=commute_only))

    sent = _planned(flow, shortlist=THREE, gym_fits=GYMS, restaurant_fits=FOOD)

    assert sent.payload.state.stay.stay_id == "stay-1"  # the shortest average commute


def test_a_seat_that_keeps_rejecting_the_query_is_dropped_and_the_ranking_goes_on_without_it():
    flow = Flow(make_seat())
    flow.geocode()
    out = flow.answer_specialists(shortlist=THREE, answer_fits=False)
    flow.rec.feed(restaurant_fit(messages(out, M.HOTEL_QUERY, Seat.RESTAURANT)[0], FOOD))
    query = messages(out, M.HOTEL_QUERY, Seat.GYM)[0]

    for _ in range(2):  # two resends are allowed
        (query,) = messages(flow.rec.feed(error_message(flow.trip, Seat.GYM, query.message_id)), M.HOTEL_QUERY, Seat.GYM)
    out = flow.rec.feed(error_message(flow.trip, Seat.GYM, query.message_id))  # the third rejection drops the seat

    (sent,) = messages(flow.answer_legs(out), M.PROPOSAL, Seat.VERIFIER)
    assert set(sent.payload.state.ranked_stays[0].scores) == {"commute", "restaurants"}
    assert any("Gym agent" in r.text for r in replies(out))


def test_a_revised_plan_reuses_the_answers_and_does_not_ask_again():
    flow = Flow(make_seat())
    sent = _planned(flow, shortlist=THREE, gym_fits=GYMS, restaurant_fits=FOOD)
    flow.rec.feed(verdict(flow.trip, VerdictStatus.BLOCK, None, [DINNER_CONFLICT], reply_to=sent.message_id))

    out = flow.rec.feed(proposal(flow.trip, Seat.RESTAURANT, revised_dinner()))

    assert messages(out, M.HOTEL_QUERY) == []
    assert messages(out, M.ROUTE_REQUEST, Seat.ROUTING)  # straight on to the legs, ranking unchanged


def test_the_ranking_is_logged_for_the_next_trip():
    memory = FakeMemory()
    _planned(Flow(make_seat(memory)), shortlist=THREE, gym_fits=GYMS, restaurant_fits=FOOD)

    (trace,) = [t for t in memory.traces if t["step"] == "ranked_hotels"]
    assert "stay-3" in trace["outcome"] and trace["agent"] is Seat.CONCIERGE


def test_the_person_gets_the_top_three_with_the_numbers_they_were_ranked_on():
    flow = Flow(make_seat())
    sent = _planned(flow, shortlist=THREE, gym_fits=GYMS, restaurant_fits=FOOD)

    out = flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash()))

    text = replies(out)[0].text
    assert "Top 3 stays" in text
    assert "1. Quiet stay 3" in text and "2. Quiet stay 2" in text and "3. Quiet stay 1" in text
    assert "nearest gym 100 m" in text and "average rank 1.5" in text and "commute 13.0 min" in text
    assert "no gym found" in text  # stay-1 has none
    assert "Recommended" in text.split("2. Quiet stay 2")[0]  # only the first is the one the plan is built around


def test_a_gym_name_keeps_its_capitals_in_the_ranking_and_the_stay_section_names_the_real_reason():
    flow = Flow(make_seat())
    sent = _planned(flow, shortlist=THREE, gym_fits=GYMS, restaurant_fits=FOOD)

    text = replies(flow.rec.feed(verdict(flow.trip, VerdictStatus.CLEAR, sent.payload.state.plan_hash())))[0].text

    assert "(Example HIIT Studio)" in text.split("Stay\n")[0]  # not "example hiit studio"
    stay_section = text.split("\nStay\n")[1].split("\n\n")[0]
    assert "What decided it: gym." in stay_section  # the Concierge's ranking chose this hotel, on the gym
