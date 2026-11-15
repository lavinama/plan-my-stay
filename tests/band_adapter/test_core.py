"""process_incoming: valid messages, replies and prose, tested with fake seats."""

import json
import logging

import pytest

from plan_my_stay.band_adapter.core import NoReply, Post
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import build_message, parse_message, serialize
from plan_my_stay.messages.examples import valid_examples
from plan_my_stay.seat import TextReply

from .conftest import NAMES, example_body, route_reply_to, route_request_for_routing
from .helpers import PERSON, STAY, FakeSeat, FakeTextSeat, only_post, run


# --- Post / NoReply -------------------------------------------------------------------------


def test_post_is_frozen():
    post = Post("hello", ("A",))
    with pytest.raises(AttributeError):
        post.content = "x"  # type: ignore[misc]


@pytest.mark.parametrize("content", ["", "   ", "\n\t", "​"])
def test_post_rejects_content_with_nothing_visible(content):
    with pytest.raises(ValueError, match="visible"):
        Post(content, ("A",))


@pytest.mark.parametrize("mentions", [(), ("",), ("  ",)])
def test_post_needs_at_least_one_real_mention(mentions):
    with pytest.raises(ValueError, match="mention"):
        Post("hello", mentions)


def test_post_rejects_a_bare_string_for_mentions():
    with pytest.raises(ValueError, match="tuple"):
        Post("hello", "Mario")  # type: ignore[arg-type]


def test_post_accepts_a_list_of_mentions_and_stores_a_tuple():
    assert Post("hello", ["A", "B"]).mentions == ("A", "B")  # type: ignore[arg-type]


def test_no_reply_instances_are_equal_whatever_the_reason():
    assert NoReply("a") == NoReply("b") == NoReply()




# --- a valid message goes to handle() --------------------------------------------------------


def test_valid_message_is_parsed_and_handed_to_handle(directory):
    request = route_request_for_routing()
    seat = FakeSeat([route_reply_to(request)])
    run(seat, serialize(request), directory=directory)
    assert seat.handled == [request]


def test_message_reply_becomes_a_post_with_serialized_body_and_mentions_from_to(directory):
    request = route_request_for_routing()
    reply = route_reply_to(request)
    post = only_post(run(FakeSeat([reply]), serialize(request), directory=directory))
    assert post.content == serialize(reply)
    assert post.mentions == (NAMES[Seat.STAY],)
    assert parse_message(post.content) == reply


def test_mentions_follow_every_recipient_in_to_in_order(directory):
    pick = parse_message(example_body("stay_pick"))  # to restaurant, gym, concierge
    post = only_post(run(FakeSeat([pick]), serialize(pick), sender=PERSON, own=Seat.STAY, directory=directory))
    assert post.mentions == (NAMES[Seat.RESTAURANT], NAMES[Seat.GYM], NAMES[Seat.CONCIERGE])


def test_several_outgoing_messages_become_several_posts_in_order(directory):
    request = route_request_for_routing()
    first, second = route_reply_to(request), route_reply_to(request)
    result = run(FakeSeat([first, second]), serialize(request), directory=directory)
    assert [p.content for p in result] == [serialize(first), serialize(second)]


def test_band_decoration_in_front_of_the_json_is_stripped(directory):
    request = route_request_for_routing()
    seat = FakeSeat([route_reply_to(request)])
    run(seat, f"[{STAY}]: @mario/plan-my-stay-routing {serialize(request)}", directory=directory)
    assert seat.handled == [request]


def test_a_tuple_or_list_of_replies_both_work(directory):
    request = route_request_for_routing()
    reply = route_reply_to(request)
    assert isinstance(run(FakeSeat((reply,)), serialize(request), directory=directory), tuple)
    assert isinstance(run(FakeSeat([reply]), serialize(request), directory=directory), tuple)


def test_empty_result_is_no_reply(directory):
    result = run(FakeSeat([]), serialize(route_request_for_routing()), directory=directory)
    assert isinstance(result, NoReply)


def test_message_not_addressed_to_the_seat_is_still_handled_but_logged(directory, caplog):
    stay_pick = parse_message(example_body("stay_pick"))  # to restaurant, gym, concierge; we are routing
    seat = FakeSeat([])
    with caplog.at_level(logging.WARNING):
        run(seat, serialize(stay_pick), directory=directory)
    assert seat.handled == [stay_pick]
    assert any("not addressed" in r.getMessage() for r in caplog.records)


def test_outgoing_message_from_another_seat_is_sent_but_warned_about(directory, caplog):
    request = route_request_for_routing()
    wrong = build_message(
        type=MessageType.ERROR, sender=Seat.GYM, to=[Seat.STAY], trip_id=request.trip_id,
        user_id=request.user_id, state_version=0, summary="x",
        payload={"code": "x"},
    )
    with caplog.at_level(logging.WARNING):
        post = only_post(run(FakeSeat([wrong]), serialize(request), directory=directory))
    assert post.content == serialize(wrong)
    assert any("from" in r.getMessage() and "gym" in r.getMessage() for r in caplog.records)




# --- text replies ----------------------------------------------------------------------------


def test_text_reply_becomes_a_post_with_its_mentions(directory):
    reply = TextReply("Here is your itinerary", (PERSON,))
    post = only_post(run(FakeSeat([reply]), serialize(route_request_for_routing()), directory=directory))
    assert post == Post("Here is your itinerary", (PERSON,))


def test_text_reply_without_mentions_falls_back_to_the_sender(directory):
    post = only_post(run(FakeSeat([TextReply("hi", ())]), serialize(route_request_for_routing()), directory=directory))
    assert post.mentions == (STAY,)


def test_blank_text_reply_is_dropped_and_logged(directory, caplog):
    seat = FakeSeat([TextReply("   ", (PERSON,))])
    with caplog.at_level(logging.WARNING):
        result = run(seat, serialize(route_request_for_routing()), directory=directory)
    assert isinstance(result, NoReply)
    assert any("blank" in r.getMessage() for r in caplog.records)


def test_blank_text_reply_does_not_drop_the_valid_posts_next_to_it(directory):
    request = route_request_for_routing()
    reply = route_reply_to(request)
    result = run(FakeSeat([TextReply("", (PERSON,)), reply]), serialize(request), directory=directory)
    assert [p.content for p in result] == [serialize(reply)]




# --- prose goes to handle_text on a TextSeat -------------------------------------------------


def test_prose_from_a_person_goes_to_handle_text_with_decoration_stripped(directory):
    seat = FakeTextSeat(text_replies=[TextReply("On it", (PERSON,))])
    result = run(seat, "[Mario Lavina]: @mario/plan-my-stay-concierge plan my trip", PERSON, own=Seat.CONCIERGE, directory=directory)
    assert seat.texts == [(PERSON, "plan my trip")]
    assert seat.handled == []
    assert only_post(result) == Post("On it", (PERSON,))


def test_json_array_or_number_from_a_person_is_still_prose(directory):
    seat = FakeTextSeat()
    run(seat, "42", PERSON, own=Seat.CONCIERGE, directory=directory)
    run(seat, "[1, 2]", PERSON, own=Seat.CONCIERGE, directory=directory)
    assert [t for _, t in seat.texts] == ["42", "[1, 2]"]


def test_empty_handle_text_result_is_no_reply(directory):
    result = run(FakeTextSeat(), "hello", PERSON, own=Seat.CONCIERGE, directory=directory)
    assert isinstance(result, NoReply)


def test_prose_from_another_seat_is_an_error_not_handle_text(directory):
    seat = FakeTextSeat()
    post = only_post(run(seat, "hello there", STAY, own=Seat.CONCIERGE, directory=directory))
    assert seat.texts == []
    error = parse_message(post.content)
    assert error.payload.code == "invalid_json"
    assert post.mentions == (STAY,)


def test_broken_json_from_another_seat_to_a_text_seat_is_an_error(directory):
    seat = FakeTextSeat()
    post = only_post(run(seat, '{"type": "request", ', STAY, own=Seat.CONCIERGE, directory=directory))
    assert seat.texts == []
    assert parse_message(post.content).payload.code == "invalid_json"


def test_a_failing_handle_text_apologises_in_prose_without_leaking_the_exception(directory, caplog):
    seat = FakeTextSeat(text_replies=RuntimeError("secret-internal-detail"))
    with caplog.at_level(logging.ERROR):
        post = only_post(run(seat, "plan please", PERSON, own=Seat.CONCIERGE, directory=directory))
    assert post.mentions == (PERSON,)
    assert "secret-internal-detail" not in post.content
    with pytest.raises(Exception):
        parse_message(post.content)  # prose, not a machine message
    assert any(r.exc_info for r in caplog.records)




# --- non-JSON to a seat without handle_text ---------------------------------------------------


def test_prose_to_a_plain_seat_gets_one_invalid_json_error_to_the_sender_seat(directory):
    post = only_post(run(FakeSeat(), "hello there", STAY, directory=directory))
    error = parse_message(post.content)
    assert error.type is MessageType.ERROR
    assert error.payload.code == "invalid_json"
    assert error.sender is Seat.ROUTING
    assert error.to == (Seat.STAY,)
    assert post.mentions == (STAY,)


def test_prose_from_a_person_to_a_plain_seat_mentions_the_person_by_display_name(directory):
    post = only_post(run(FakeSeat(), "hello there", PERSON, directory=directory))
    error = parse_message(post.content)
    assert error.payload.code == "invalid_json"
    assert post.mentions == (PERSON,)
    assert Seat.ROUTING not in error.to  # `to` is only nominal for a person; the mention routes it


def test_invalid_json_object_from_a_person_to_the_concierge_gets_an_error_with_a_valid_nominal_to(directory):
    seat = FakeTextSeat()
    post = only_post(run(seat, '{"type": "request"}', PERSON, own=Seat.CONCIERGE, directory=directory))
    error = parse_message(post.content)
    assert seat.texts == []  # a JSON object that fails validation is a rejected machine message, not prose
    assert error.payload.code == "validation_failed"
    assert Seat.CONCIERGE not in error.to  # `to` is only nominal for a person, and may not name the sender
    assert post.mentions == (PERSON,)




# --- sizes are logged ------------------------------------------------------------------------


def test_the_size_of_every_outgoing_body_is_logged(directory, caplog):
    request = route_request_for_routing()
    reply = route_reply_to(request)
    body = serialize(reply)
    with caplog.at_level(logging.INFO):
        run(FakeSeat([reply, TextReply("héllo", (PERSON,))]), serialize(request), directory=directory)
    messages = [r.getMessage() for r in caplog.records]
    assert any(f"{len(body.encode())} bytes" in m and f"{len(body)} chars" in m for m in messages)
    assert any(f"{len('héllo'.encode())} bytes" in m for m in messages)


def test_the_bodies_themselves_are_not_logged(directory, caplog):
    request = route_request_for_routing()
    reply = route_reply_to(request)
    with caplog.at_level(logging.DEBUG):
        run(FakeSeat([reply, TextReply("a private itinerary line", (PERSON,))]), serialize(request), directory=directory)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert serialize(reply) not in logged
    assert "a private itinerary line" not in logged




# --- every example message survives the trip -------------------------------------------------


@pytest.mark.parametrize("name", sorted(valid_examples()))
def test_every_valid_example_goes_out_intact_and_parses_back(name, directory):
    message = parse_message(json.dumps(valid_examples()[name]))
    own = message.to[0]
    post = only_post(run(FakeSeat([message]), serialize(route_request_for_routing()), directory=directory, own=own))
    assert parse_message(post.content) == message
    assert post.mentions == tuple(NAMES[s] for s in message.to)
