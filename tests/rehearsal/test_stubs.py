"""The stand-in specialists answer a request with valid, contract-shaped messages (docs/PLAN.md: each agent ships a stub)."""

import json

from plan_my_stay import demo
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import build_message, parse_message, serialize
from plan_my_stay.rehearsal.stubs import GymStub, RestaurantStub, StayStub


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


def test_stay_stub_returns_a_ranked_shortlist_within_budget_and_a_stay_pick():
    out = StayStub().handle(_request(Seat.STAY, budget=1000, response_type="stay_shortlist"))
    by_type = _by_type(out)

    stays = by_type[MessageType.STAY_SHORTLIST].payload.stays
    assert [s.rank for s in stays] == list(range(1, len(stays) + 1))
    assert all(s.total_price <= 1000 for s in stays) and len(stays) >= 2
    assert by_type[MessageType.STAY_SHORTLIST].to == (Seat.CONCIERGE,)
    pick = by_type[MessageType.STAY_PICK]
    assert set(pick.to) == {Seat.RESTAURANT, Seat.GYM, Seat.CONCIERGE}
    assert pick.payload.stay_id == stays[0].stay_id


def test_stay_stub_drops_stays_priced_above_the_budget():
    out = StayStub().handle(_request(Seat.STAY, budget=700, response_type="stay_shortlist"))
    assert all(s.total_price <= 700 for s in _by_type(out)[MessageType.STAY_SHORTLIST].payload.stays)


def test_stay_stub_has_nothing_to_say_to_a_zone_fit():
    zone_fit = _by_type(RestaurantStub().handle(_request(Seat.RESTAURANT)))[MessageType.ZONE_FIT]
    assert StayStub().handle(zone_fit) == []


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


def test_gym_stub_posts_the_workout_slot_after_meeting_b_and_a_matching_proposal():
    out = _by_type(GymStub().handle(_request(Seat.GYM)))
    slot = out[MessageType.WORKOUT_SLOT]

    assert (slot.payload.start, slot.payload.end) == ("17:30", "18:15")  # 07:00 is impossible on check-in day
    assert set(slot.to) == {Seat.RESTAURANT, Seat.CONCIERGE}
    item = out[MessageType.PROPOSAL].payload.items[0]
    assert (item.when.start, item.when.end) == ("17:30", "18:15")
    assert out[MessageType.ZONE_FIT].to == (Seat.STAY,)


def test_every_stub_message_round_trips_through_the_contract():
    for stub, seat in ((StayStub(), Seat.STAY), (RestaurantStub(), Seat.RESTAURANT), (GymStub(), Seat.GYM)):
        request = _request(seat, budget=1000, response_type="stay_shortlist" if seat is Seat.STAY else "proposal")
        for message in stub.handle(request):
            assert parse_message(serialize(message)) == message
            assert message.reply_to == request.message_id and message.trip_id == demo.TRIP_ID
            json.loads(serialize(message))
