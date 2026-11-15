"""The stand-in specialists answer a request with valid, contract-shaped messages (docs/PLAN.md: each agent ships a stub)."""

import json
import math
from statistics import mean

import pytest

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.geo import haversine_m, walk_minutes
from plan_my_stay.messages import build_message, parse_message, serialize
from plan_my_stay.rehearsal.stubs import GymStub, RestaurantStub, StayStub

CHELSEA, TRIBECA, FIDI = "stay-chelsea-1", "stay-tribeca-1", "stay-fidi-2"
ONE_DEGREE_M = math.pi / 180 * 6_371_000.0
WASHINGTON_HEIGHTS = (40.85, -73.93)  # 10 km north of the last gym and the last restaurant


def _request(to: Seat, *, round_: int = 0, budget: float | None = None, response_type: str = "proposal"):
    state = demo.demo_state()
    payload = {
        "city": state.city,
        "dates": {"check_in": state.check_in, "check_out": state.check_out},
        "anchors": state.anchors,
        "zones": state.zones,
        "slots": state.slots,
        "profile": state.profile,
        "round": round_,
        "response_type": response_type,
    }
    if budget is not None:
        payload["budget"] = {"currency": "USD", "amount": budget}
    return build_message(
        type=MessageType.REQUEST, sender=Seat.CONCIERGE, to=[to], trip_id=demo.TRIP_ID, user_id=demo.USER_ID,
        state_version=1, summary="request", payload=payload,
    )


def _by_type(messages):
    return {m.type: m for m in messages}


def _zone_centre(zone_id: str) -> tuple[float, float]:
    """Where the Concierge measures from when a listing has no address: the middle of the zone's box."""
    zone = next(z for z in demo.demo_zones() if z.id == zone_id)
    return (zone.bounds.ne.lat + zone.bounds.sw.lat) / 2, (zone.bounds.ne.lng + zone.bounds.sw.lng) / 2


def _hotel(stay_id: str, zone_id: str, at: tuple[float, float] | None = None) -> dict:
    lat, lng = at if at is not None else _zone_centre(zone_id)
    return {"stay_id": stay_id, "zone_id": zone_id, "lat": lat, "lng": lng, "location_precision": "zone"}


def _demo_hotels() -> list[dict]:
    return [_hotel(CHELSEA, "z1"), _hotel(TRIBECA, "z2"), _hotel(FIDI, "z3")]


def _hotel_query(hotels: list[dict], *, radius_minutes: float | None = None):
    payload: dict = {"hotels": hotels}
    if radius_minutes is not None:
        payload["radius_minutes"] = radius_minutes
    return build_message(
        type=MessageType.HOTEL_QUERY, sender=Seat.CONCIERGE, to=[Seat.GYM, Seat.RESTAURANT], trip_id=demo.TRIP_ID,
        user_id=demo.USER_ID, state_version=2, summary="hotel query", payload=payload,
    )


def _gym_fit(hotels: list[dict]):
    (reply,) = GymStub().handle(_hotel_query(hotels))
    return reply


def _restaurant_fit(hotels: list[dict], *, radius_minutes: float | None = None):
    (reply,) = RestaurantStub().handle(_hotel_query(hotels, radius_minutes=radius_minutes))
    return reply


def _by_stay(entries):
    return {e.stay_id: e for e in entries}


def _average_commute(stay) -> float:
    return mean(min(stay.commute_minutes[anchor].values()) for anchor in ("B", "C"))


# --- Stay -------------------------------------------------------------------------------------------------------


def test_stay_stub_returns_a_ranked_shortlist_within_budget_and_a_stay_pick():
    out = StayStub().handle(_request(Seat.STAY, budget=1000, response_type="stay_shortlist"))
    by_type = _by_type(out)

    stays = by_type[MessageType.STAY_SHORTLIST].payload.stays
    assert [s.rank for s in stays] == list(range(1, len(stays) + 1))
    assert all(s.total_price <= 1000 for s in stays) and len(stays) >= 3
    assert by_type[MessageType.STAY_SHORTLIST].to == (Seat.CONCIERGE,)
    pick = by_type[MessageType.STAY_PICK]
    assert set(pick.to) == {Seat.RESTAURANT, Seat.GYM, Seat.CONCIERGE}
    assert pick.payload.stay_id == stays[0].stay_id


def test_stay_stub_ranks_the_stays_by_average_commute_with_chelsea_first():
    stays = _by_type(StayStub().handle(_request(Seat.STAY, budget=1000)))[MessageType.STAY_SHORTLIST].payload.stays

    assert [s.stay_id for s in stays] == [CHELSEA, TRIBECA, FIDI]
    assert [s.zone_id for s in stays] == ["z1", "z2", "z3"]
    assert [s.average_commute_minutes for s in stays] == pytest.approx([11.5, 12.5, 13.0])
    assert all(s.deciding_factor == "commute" for s in stays)


def test_stay_stub_average_commute_is_the_mean_of_the_fastest_mode_to_meetings_b_and_c():
    stays = _by_type(StayStub().handle(_request(Seat.STAY, budget=5000)))[MessageType.STAY_SHORTLIST].payload.stays

    assert len(stays) == 4
    assert all(s.average_commute_minutes == pytest.approx(_average_commute(s)) for s in stays)
    assert [s.average_commute_minutes for s in stays] == sorted(s.average_commute_minutes for s in stays)


def test_stay_stub_drops_stays_priced_above_the_budget():
    within = _by_type(StayStub().handle(_request(Seat.STAY, budget=700)))[MessageType.STAY_SHORTLIST].payload.stays
    assert [s.stay_id for s in within] == [CHELSEA]

    ids = {s.stay_id for s in _by_type(StayStub().handle(_request(Seat.STAY, budget=1000)))[MessageType.STAY_SHORTLIST].payload.stays}
    assert "stay-fidi-1" not in ids  # the penthouse costs $1,200


def test_stay_stub_without_a_budget_keeps_every_stay_and_still_ranks_by_commute():
    stays = _by_type(StayStub().handle(_request(Seat.STAY)))[MessageType.STAY_SHORTLIST].payload.stays
    assert len(stays) == 4
    assert [s.rank for s in stays] == [1, 2, 3, 4]
    assert [s.average_commute_minutes for s in stays] == sorted(s.average_commute_minutes for s in stays)


def test_stay_stub_with_nothing_in_budget_sends_an_empty_shortlist_and_no_stay_pick():
    out = _by_type(StayStub().handle(_request(Seat.STAY, budget=100)))
    assert out[MessageType.STAY_SHORTLIST].payload.stays == ()
    assert MessageType.STAY_PICK not in out


def test_stay_stub_has_nothing_to_say_to_a_zone_fit():
    zone_fit = _by_type(RestaurantStub().handle(_request(Seat.RESTAURANT)))[MessageType.ZONE_FIT]
    assert StayStub().handle(zone_fit) == []


def test_stay_stub_ignores_a_hotel_query_which_is_not_addressed_to_it():
    assert StayStub().handle(_hotel_query(_demo_hotels())) == []


# --- Restaurant: request and conflict ----------------------------------------------------------------------------


def test_restaurant_stub_first_pick_closes_before_dinner_ends_so_the_verifier_can_block_it():
    out = _by_type(RestaurantStub().handle(_request(Seat.RESTAURANT)))
    item = out[MessageType.PROPOSAL].payload.items[0]

    assert item.hours["Tue"] == "11:00-19:00" and item.when.end == "19:45"
    assert set(out[MessageType.PROPOSAL].to) == {Seat.CONCIERGE, Seat.VERIFIER}
    assert out[MessageType.ZONE_FIT].to == (Seat.STAY,)
    learned = out[MessageType.PREFERENCE_LEARNED]
    assert learned.payload.key == "meal_timing" and set(learned.to) == {Seat.CONCIERGE, Seat.GYM}


def test_restaurant_stub_replaces_a_blocked_pick_when_the_verifier_sends_a_conflict():
    first = _by_type(RestaurantStub().handle(_request(Seat.RESTAURANT)))[MessageType.PROPOSAL]
    conflict = build_message(
        type=MessageType.CONFLICT, sender=Seat.VERIFIER, to=[Seat.RESTAURANT], trip_id=demo.TRIP_ID, user_id=demo.USER_ID,
        state_version=2, summary="closes early", reply_to=first.message_id,
        payload={"rule": "open_during_slot", "item_id": first.payload.items[0].item_id, "detail": "closes at 19:00"},
    )

    revised = RestaurantStub().handle(conflict)

    assert len(revised) == 1 and revised[0].type is MessageType.PROPOSAL
    item = revised[0].payload.items[0]
    assert item.hours["Tue"] == "11:00-22:00" and item.item_id != first.payload.items[0].item_id
    assert revised[0].reply_to == conflict.message_id


def test_restaurant_stub_can_start_with_a_good_pick():
    out = _by_type(RestaurantStub(first_pick_closes_early=False).handle(_request(Seat.RESTAURANT)))
    assert out[MessageType.PROPOSAL].payload.items[0].hours["Tue"] == "11:00-22:00"


def test_restaurant_stub_ignores_messages_that_are_neither_a_request_a_conflict_nor_a_hotel_query():
    workout_slot = _by_type(GymStub().handle(_request(Seat.GYM)))[MessageType.WORKOUT_SLOT]
    assert RestaurantStub().handle(workout_slot) == []


# --- Gym: request -------------------------------------------------------------------------------------------------


def test_gym_stub_posts_the_workout_slot_after_meeting_b_and_a_matching_proposal():
    out = _by_type(GymStub().handle(_request(Seat.GYM)))
    slot = out[MessageType.WORKOUT_SLOT]

    assert (slot.payload.start, slot.payload.end) == ("17:30", "18:15")  # 07:00 is impossible on check-in day
    assert set(slot.to) == {Seat.RESTAURANT, Seat.CONCIERGE}
    item = out[MessageType.PROPOSAL].payload.items[0]
    assert (item.when.start, item.when.end) == ("17:30", "18:15")
    assert out[MessageType.ZONE_FIT].to == (Seat.STAY,)


def test_gym_stub_ignores_messages_that_are_neither_a_request_nor_a_hotel_query():
    zone_fit = _by_type(RestaurantStub().handle(_request(Seat.RESTAURANT)))[MessageType.ZONE_FIT]
    assert GymStub().handle(zone_fit) == []


# --- Gym: hotel_query -> gym_fit ----------------------------------------------------------------------------------


def test_gym_stub_answers_a_hotel_query_with_a_gym_fit_for_the_concierge():
    query = _hotel_query(_demo_hotels())

    (reply,) = GymStub().handle(query)

    assert reply.type is MessageType.GYM_FIT and reply.sender is Seat.GYM and reply.to == (Seat.CONCIERGE,)
    assert reply.reply_to == query.message_id
    assert (reply.trip_id, reply.user_id, reply.state_version) == (query.trip_id, query.user_id, query.state_version)
    assert [e.stay_id for e in reply.payload.hotels] == [CHELSEA, TRIBECA, FIDI]  # the query's order


def test_gym_fit_reports_the_straight_line_distance_to_the_nearest_gym_and_the_walk():
    entries = _by_stay(_gym_fit(_demo_hotels()).payload.hotels)

    chelsea = entries[CHELSEA].nearest_gym
    demo_gym = demo.demo_gym_item()  # the same gym the workout proposal uses
    assert (chelsea.item_id, chelsea.name, chelsea.lat, chelsea.lng) == (demo_gym.item_id, demo_gym.name, demo_gym.lat, demo_gym.lng)
    assert (chelsea.lat, chelsea.lng) == (40.7488, -73.9971)
    assert chelsea.distance_m == pytest.approx(haversine_m(*_zone_centre("z1"), 40.7488, -73.9971))
    assert chelsea.distance_m == pytest.approx(316, abs=1)
    assert chelsea.walk_minutes == pytest.approx(walk_minutes(chelsea.distance_m))

    for entry in entries.values():
        gym = entry.nearest_gym
        assert gym.walk_minutes == pytest.approx(gym.distance_m * 1.3 / 80)


def test_gym_fit_picks_the_nearest_gym_not_just_any_gym_in_reach():
    union_square = (40.7360, -73.9912)  # 20 m from Union Square Iron Works, 2.5 km from Example HIIT Studio
    entry = _gym_fit([_hotel("stay-x", "z1", union_square)]).payload.hotels[0]

    assert entry.nearest_gym.name == "Union Square Iron Works"
    assert entry.nearest_gym.distance_m < 50


def test_gym_fit_makes_the_demos_intended_ordering_chelsea_then_fidi_then_tribeca():
    # The demo's story: TriBeCa is second on commute but its gym is a trek, so the Financial District hotel overtakes it.
    entries = _by_stay(_gym_fit(_demo_hotels()).payload.hotels)
    chelsea, tribeca, fidi = (entries[s].nearest_gym.distance_m for s in (CHELSEA, TRIBECA, FIDI))

    assert chelsea < fidi < tribeca
    assert fidi == pytest.approx(350, abs=25)  # a gym within about 400 m of the Financial District hotel
    assert tribeca == pytest.approx(1000, abs=75)  # about a kilometre from TriBeCa
    assert entries[TRIBECA].nearest_gym.name == entries[FIDI].nearest_gym.name == "Wall Street Strength Club"


@pytest.mark.parametrize(("metres_south", "found"), [(2_900, True), (3_100, False)])
def test_gym_fit_has_no_nearest_gym_when_none_is_within_3_km(metres_south: float, found: bool):
    wall_street_gym = (40.7115, -74.0105)
    hotel = (wall_street_gym[0] - metres_south / ONE_DEGREE_M, wall_street_gym[1])  # due south, so the distance is exact

    entry = _gym_fit([_hotel("stay-x", "z3", hotel)]).payload.hotels[0]

    assert (entry.nearest_gym is not None) is found
    if found:
        assert entry.nearest_gym.distance_m == pytest.approx(metres_south, abs=2)


def test_gym_fit_gives_every_queried_hotel_an_entry_even_when_there_is_no_gym():
    hotels = [*_demo_hotels(), _hotel("stay-far", "z1", WASHINGTON_HEIGHTS)]
    entries = _gym_fit(hotels).payload.hotels

    assert [e.stay_id for e in entries] == [CHELSEA, TRIBECA, FIDI, "stay-far"]
    assert entries[-1].nearest_gym is None


# --- Restaurant: hotel_query -> restaurant_fit --------------------------------------------------------------------


def test_restaurant_stub_answers_a_hotel_query_with_a_restaurant_fit_for_the_concierge():
    query = _hotel_query(_demo_hotels())

    (reply,) = RestaurantStub().handle(query)

    assert reply.type is MessageType.RESTAURANT_FIT and reply.sender is Seat.RESTAURANT and reply.to == (Seat.CONCIERGE,)
    assert reply.reply_to == query.message_id
    assert (reply.trip_id, reply.user_id, reply.state_version) == (query.trip_id, query.user_id, query.state_version)
    assert reply.payload.radius_minutes == 10  # the query's default is echoed
    assert [e.stay_id for e in reply.payload.hotels] == [CHELSEA, TRIBECA, FIDI]


def test_restaurant_fit_echoes_the_radius_the_concierge_asked_for():
    assert _restaurant_fit(_demo_hotels(), radius_minutes=15).payload.radius_minutes == 15


def test_restaurant_fit_counts_the_restaurants_within_ten_minutes_and_averages_their_ranks():
    entries = _by_stay(_restaurant_fit(_demo_hotels()).payload.hotels)

    chelsea, tribeca, fidi = entries[CHELSEA], entries[TRIBECA], entries[FIDI]
    assert (chelsea.count, chelsea.average_rank) == (3, pytest.approx(8 / 3))
    assert (tribeca.count, tribeca.average_rank) == (2, pytest.approx(7.5))
    assert (fidi.count, fidi.average_rank) == (3, pytest.approx(13 / 3))


def test_restaurant_fit_lists_the_best_ranked_first_with_their_walks():
    chelsea = _by_stay(_restaurant_fit(_demo_hotels()).payload.hotels)[CHELSEA]

    first = chelsea.top[0]
    assert first.name == "Urban Vegan Kitchen"
    assert [r.rank for r in chelsea.top] == [1, 2, 5]
    demo_restaurant = demo.demo_restaurant_item()  # the same restaurant the dinner proposal uses
    assert (first.item_id, first.name) == (demo_restaurant.item_id, demo_restaurant.name)
    assert first.walk_minutes == pytest.approx(walk_minutes(haversine_m(*_zone_centre("z1"), 40.7445, -73.9983)))
    assert (demo_restaurant.lat, demo_restaurant.lng) == (40.7445, -73.9983)
    assert all(r.walk_minutes <= 10 for entry in _restaurant_fit(_demo_hotels()).payload.hotels for r in entry.top)


def test_restaurant_fit_cuts_off_at_the_radius():
    # At 6 minutes the straight-line reach is 461 m: of Chelsea's three (313, 404 and 419 m) only the 5.1-minute one is left.
    entry = _by_stay(_restaurant_fit(_demo_hotels(), radius_minutes=6).payload.hotels)[CHELSEA]

    assert entry.count == 1 and entry.average_rank == 1.0
    assert [r.name for r in entry.top] == ["Urban Vegan Kitchen"]


def test_restaurant_fit_with_no_restaurant_in_reach_has_a_count_of_zero_and_no_average_rank():
    entry = _restaurant_fit([_hotel("stay-far", "z1", WASHINGTON_HEIGHTS)]).payload.hotels[0]

    assert entry.count == 0 and entry.average_rank is None and entry.top == ()


def test_restaurant_fit_top_holds_the_three_best_ranked_whatever_the_count():
    # A 90-minute radius reaches all eight fixture restaurants from Chelsea.
    entry = _by_stay(_restaurant_fit(_demo_hotels(), radius_minutes=90).payload.hotels)[CHELSEA]

    assert entry.count == 8 and entry.average_rank == pytest.approx(4.5)
    assert [r.rank for r in entry.top] == [1, 2, 3]


def test_restaurant_fit_makes_the_demos_intended_ordering_chelsea_then_fidi_then_tribeca():
    entries = _by_stay(_restaurant_fit(_demo_hotels()).payload.hotels)

    assert entries[CHELSEA].average_rank < entries[FIDI].average_rank < entries[TRIBECA].average_rank
    assert entries[TRIBECA].count < entries[FIDI].count  # TriBeCa has few restaurants and they rank low (7th and 8th)
    assert min(r.rank for r in entries[TRIBECA].top) >= 7


# --- The demo's intended outcome ---------------------------------------------------------------------------------


def test_the_specialists_numbers_lift_the_financial_district_hotel_above_tribeca():
    """Commute alone orders the hotels Chelsea (11.5 min), TriBeCa (12.5), Financial District (13.0). With the
    Concierge's default weights (commute 0.5, gym 0.25, restaurants 0.25; each factor scored best/this, 1 = best) the
    gym and restaurant numbers above must flip the last two, and keep Chelsea first."""
    stays = _by_type(StayStub().handle(_request(Seat.STAY, budget=1000)))[MessageType.STAY_SHORTLIST].payload.stays
    commute = {s.stay_id: s.average_commute_minutes for s in stays}
    gym = {e.stay_id: e.nearest_gym.distance_m for e in _gym_fit(_demo_hotels()).payload.hotels}
    rank = {e.stay_id: e.average_rank for e in _restaurant_fit(_demo_hotels()).payload.hotels}

    def score(stay_id: str) -> float:
        return sum(
            weight * min(values.values()) / values[stay_id]
            for weight, values in ((0.5, commute), (0.25, gym), (0.25, rank))
        )

    assert commute[CHELSEA] < commute[TRIBECA] < commute[FIDI]
    scores = {s: score(s) for s in (CHELSEA, TRIBECA, FIDI)}
    assert scores[CHELSEA] > scores[FIDI] > scores[TRIBECA]
    assert scores[FIDI] - scores[TRIBECA] > 0.1  # a clear overtake, not a coin flip


# --- Every message is valid ---------------------------------------------------------------------------------------


def test_every_stub_message_round_trips_through_the_contract():
    for stub, seat in ((StayStub(), Seat.STAY), (RestaurantStub(), Seat.RESTAURANT), (GymStub(), Seat.GYM)):
        request = _request(seat, budget=1000, response_type="stay_shortlist" if seat is Seat.STAY else "proposal")
        for message in stub.handle(request):
            assert parse_message(serialize(message)) == message
            assert message.reply_to == request.message_id and message.trip_id == demo.TRIP_ID
            json.loads(serialize(message))


def test_every_hotel_query_reply_round_trips_through_the_contract():
    query = _hotel_query([*_demo_hotels(), _hotel("stay-far", "z1", WASHINGTON_HEIGHTS)])

    for message in (*GymStub().handle(query), *RestaurantStub().handle(query)):
        assert parse_message(serialize(message)) == message
        json.loads(serialize(message))


def test_stay_stub_tells_the_concierge_a_preference_it_learned_so_run_two_has_something_to_remember():
    out = _by_type(StayStub().handle(_request(Seat.STAY, budget=1000, response_type="stay_shortlist")))
    learned = out[MessageType.PREFERENCE_LEARNED]

    assert learned.to == (Seat.CONCIERGE,)
    assert (learned.payload.key, learned.payload.value) == ("stay_noise", "quiet")
    assert learned.payload.evidence


def test_stay_stub_learns_it_once_not_again_when_asked_for_cheaper_stays():
    retry = _request(Seat.STAY, round_=1, budget=870, response_type="stay_shortlist")

    assert MessageType.PREFERENCE_LEARNED not in _by_type(StayStub().handle(retry))
