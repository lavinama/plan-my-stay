"""process_incoming: rejected messages, the retry cap, crashing seats and the cases with nobody to answer."""

import json
import logging
import threading
import uuid

import pytest

from plan_my_stay.band_adapter import core
from plan_my_stay.band_adapter.core import NoReply, RetryTracker
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import parse_message, serialize
from plan_my_stay.seat import TextReply

from .conftest import NAMES, example_body, example_raw, route_request_for_routing
from .helpers import PERSON, STAY, FakeSeat, FakeTextSeat, only_post, run


# --- invalid messages get one error reply ----------------------------------------------------


def _bad(**overrides) -> str:
    return example_body("route_request", **overrides)


def test_invalid_message_gets_one_error_reply_addressed_to_the_sender_seat(directory):
    bad_id = str(uuid.uuid4())
    text = _bad(message_id=bad_id, payload={"origin": "nope"})
    post = only_post(run(FakeSeat(), text, STAY, directory=directory))
    error = parse_message(post.content)
    assert error.type is MessageType.ERROR
    assert error.payload.code == "validation_failed"
    assert error.payload.bad_message_id == bad_id
    assert error.reply_to == bad_id
    assert any(f.startswith("payload.") for f in error.payload.fields)
    assert error.to == (Seat.STAY,)
    assert post.mentions == (STAY,)


def test_error_reply_copies_trip_user_and_state_version_from_the_bad_message(directory):
    raw = example_raw("route_request", state_version=7, trip_id="trip-xyz", user_id="user-xyz", payload={})
    post = only_post(run(FakeSeat(), json.dumps(raw), STAY, directory=directory))
    error = parse_message(post.content)
    assert (error.trip_id, error.user_id, error.state_version) == ("trip-xyz", "user-xyz", 7)


def test_error_reply_uses_placeholders_when_the_bad_message_has_no_envelope(directory):
    post = only_post(run(FakeSeat(), "not json at all", STAY, directory=directory))
    error = parse_message(post.content)
    assert (error.trip_id, error.user_id, error.state_version) == ("unknown", "unknown", 0)


def test_error_goes_to_the_seat_named_in_from_even_when_a_person_posted_it(directory):
    text = _bad(payload={}, **{"from": "gym"})
    post = only_post(run(FakeSeat(), text, PERSON, directory=directory))
    assert parse_message(post.content).to == (Seat.GYM,)
    assert post.mentions == (NAMES[Seat.GYM],)


def test_unexpected_sender_is_answered_to_that_seat(directory):
    text = example_body("request", **{"from": "stay", "to": ["routing"]})  # only the Concierge sends requests
    post = only_post(run(FakeSeat(), text, STAY, directory=directory))
    error = parse_message(post.content)
    assert error.payload.code == "unexpected_sender"
    assert error.to == (Seat.STAY,)


def test_unknown_type_and_unsupported_version_are_answered(directory):
    unknown = only_post(run(FakeSeat(), _bad(type="telepathy"), STAY, directory=directory))
    assert parse_message(unknown.content).payload.code == "unknown_type"
    version = only_post(run(FakeSeat(), _bad(schema_version="9.0"), STAY, directory=directory))
    assert parse_message(version.content).payload.code == "unsupported_version"


def test_from_naming_the_receiving_seat_itself_falls_back_to_the_real_sender(directory):
    text = _bad(payload={}, **{"from": "routing"})  # claims to be us
    post = only_post(run(FakeSeat(), text, NAMES[Seat.GYM], directory=directory))
    assert parse_message(post.content).to == (Seat.GYM,)
    assert post.mentions == (NAMES[Seat.GYM],)


def test_unknown_from_value_uses_the_band_sender_when_it_is_a_seat(directory):
    text = _bad(payload={}, **{"from": "intern"})
    post = only_post(run(FakeSeat(), text, NAMES[Seat.GYM], directory=directory))
    assert parse_message(post.content).to == (Seat.GYM,)


def test_a_non_uuid_bad_message_id_still_gets_an_error_reply(directory):
    text = _bad(message_id="not-a-uuid", payload={})
    post = only_post(run(FakeSeat(), text, STAY, directory=directory))
    error = parse_message(post.content)
    assert error.type is MessageType.ERROR
    assert error.reply_to is None  # reply_to must be a UUID
    assert error.payload.bad_message_id == "not-a-uuid"  # the sender can still match the error to its message


def test_handle_is_never_called_for_a_rejected_message(directory):
    seat = FakeSeat()
    run(seat, _bad(payload={}), STAY, directory=directory)
    run(seat, "prose", STAY, directory=directory)
    assert seat.handled == []




# --- retry cap -------------------------------------------------------------------------------


def test_retry_tracker_answers_the_original_and_two_retries_then_stops():
    tracker = RetryTracker(max_retries=2)
    assert [tracker.allow_reply("a", "x") for _ in range(5)] == [True, True, True, False, False]


def test_retry_tracker_counts_per_sender_and_per_bad_message():
    tracker = RetryTracker(max_retries=0)
    assert tracker.allow_reply("a", "x") is True
    assert tracker.allow_reply("a", "x") is False
    assert tracker.allow_reply("b", "x") is True
    assert tracker.allow_reply("a", "y") is True


def test_retry_tracker_forgets_the_oldest_entries_beyond_its_capacity():
    tracker = RetryTracker(max_retries=0, capacity=2)
    tracker.allow_reply("a", "1")
    tracker.allow_reply("a", "2")
    tracker.allow_reply("a", "3")  # evicts "1"
    assert tracker.allow_reply("a", "1") is True


def test_retry_tracker_is_safe_across_threads():
    tracker = RetryTracker(max_retries=2)
    results: list[bool] = []

    def hit() -> None:
        results.append(tracker.allow_reply("a", "x"))

    threads = [threading.Thread(target=hit) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 3


def test_retry_tracker_rejects_nonsense_settings():
    with pytest.raises(ValueError):
        RetryTracker(max_retries=-1)
    with pytest.raises(ValueError):
        RetryTracker(capacity=0)


def test_the_same_bad_message_is_answered_three_times_then_silent(directory):
    retries = RetryTracker()
    bad_id = str(uuid.uuid4())
    text = _bad(message_id=bad_id, payload={})
    answers = [run(FakeSeat(), text, STAY, directory=directory, retries=retries) for _ in range(4)]
    assert [isinstance(a, tuple) for a in answers] == [True, True, True, False]
    assert isinstance(answers[3], NoReply)


def test_the_retry_cap_logs_when_it_goes_silent(directory, caplog):
    retries = RetryTracker(max_retries=0)
    text = _bad(payload={})
    with caplog.at_level(logging.WARNING):
        run(FakeSeat(), text, STAY, directory=directory, retries=retries)
        run(FakeSeat(), text, STAY, directory=directory, retries=retries)
    assert any("staying silent" in r.getMessage() for r in caplog.records)


def test_different_senders_have_separate_retry_budgets(directory):
    retries = RetryTracker(max_retries=0)
    text = _bad(message_id=str(uuid.uuid4()), payload={})
    assert isinstance(run(FakeSeat(), text, STAY, directory=directory, retries=retries), tuple)
    assert isinstance(run(FakeSeat(), text, NAMES[Seat.GYM], directory=directory, retries=retries), tuple)


def test_garbage_without_an_id_is_capped_by_a_body_digest(directory):
    retries = RetryTracker(max_retries=1)
    results = [run(FakeSeat(), "same garbage", STAY, directory=directory, retries=retries) for _ in range(3)]
    assert [isinstance(r, tuple) for r in results] == [True, True, False]
    other = run(FakeSeat(), "different garbage", STAY, directory=directory, retries=retries)
    assert isinstance(other, tuple)




# --- exceptions inside handle ----------------------------------------------------------------


def test_exception_in_handle_sends_an_internal_error_to_the_sender_and_is_logged(directory, caplog):
    request = route_request_for_routing()
    seat = FakeSeat(RuntimeError("boom-secret"))
    with caplog.at_level(logging.ERROR):
        post = only_post(run(seat, serialize(request), directory=directory, sender=STAY))
    error = parse_message(post.content)
    assert error.type is MessageType.ERROR
    assert error.payload.code == "internal_error"
    assert error.payload.bad_message_id == request.message_id
    assert error.reply_to == request.message_id
    assert error.to == (Seat.STAY,)
    assert (error.trip_id, error.user_id, error.state_version) == (request.trip_id, request.user_id, request.state_version)
    assert post.mentions == (STAY,)
    assert "boom-secret" not in post.content
    record = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert record.exc_info is not None
    text = record.getMessage()
    assert "routing" in text and "route_request" in text and request.message_id in text and request.trip_id in text


def test_handle_returning_none_is_an_internal_error_not_a_crash(directory):
    post = only_post(run(FakeSeat(None), serialize(route_request_for_routing()), directory=directory))
    assert parse_message(post.content).payload.code == "internal_error"


def test_handle_returning_something_that_is_not_a_message_is_an_internal_error(directory):
    post = only_post(run(FakeSeat(["just a string"]), serialize(route_request_for_routing()), directory=directory))
    assert parse_message(post.content).payload.code == "internal_error"


def test_internal_error_when_the_message_claims_to_be_from_this_seat(directory):
    request = route_request_for_routing()  # from stay
    seat = FakeSeat(RuntimeError("x"))
    post = only_post(run(seat, serialize(request), NAMES[Seat.GYM], directory=directory, own=Seat.STAY))
    assert parse_message(post.content).to == (Seat.GYM,)  # `to` may not name the replying seat itself




# --- nobody to answer, and guards ------------------------------------------------------------


def test_a_rejected_message_from_an_unknown_sender_with_no_from_cannot_be_answered(directory, caplog):
    with caplog.at_level(logging.ERROR):
        result = run(FakeSeat(), "garbage", "", directory=directory)
    assert isinstance(result, NoReply)
    assert any("cannot answer" in r.getMessage() for r in caplog.records)


def test_a_handler_crash_with_no_one_to_tell_is_a_logged_no_reply(directory, caplog):
    request = route_request_for_routing()  # claims to be from stay, and we are stay
    with caplog.at_level(logging.ERROR):
        result = run(FakeSeat(RuntimeError("x")), serialize(request), "", own=Seat.STAY, directory=directory)
    assert isinstance(result, NoReply)
    assert any("no one to tell" in r.getMessage() for r in caplog.records)


def test_a_text_reply_with_nobody_to_mention_is_dropped(directory, caplog):
    seat = FakeTextSeat(text_replies=[TextReply("hello", ())])
    with caplog.at_level(logging.ERROR):
        result = run(seat, "hi", "", own=Seat.CONCIERGE, directory=directory)
    assert isinstance(result, NoReply)
    assert any("nobody to mention" in r.getMessage() for r in caplog.records)


def test_a_failing_handle_text_for_an_unnamed_sender_is_a_no_reply(directory):
    result = run(FakeTextSeat(text_replies=RuntimeError("x")), "hi", "", own=Seat.CONCIERGE, directory=directory)
    assert isinstance(result, NoReply)


def test_an_error_reply_that_cannot_be_built_is_a_logged_no_reply_not_a_crash(directory, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("cannot build")

    monkeypatch.setattr(core, "build_error_reply", broken)
    with caplog.at_level(logging.ERROR):
        result = run(FakeSeat(), "prose", STAY, directory=directory)
    assert isinstance(result, NoReply)
    assert any("could not build an error reply" in r.getMessage() and r.exc_info for r in caplog.records)


def test_envelope_hints_survive_braces_that_are_not_json(directory):
    post = only_post(run(FakeSeat(), "look { not json } here", STAY, directory=directory))
    assert (parse_message(post.content).trip_id, parse_message(post.content).state_version) == ("unknown", 0)


@pytest.mark.parametrize("version", [True, -1, "3", 1.5])
def test_a_nonsense_state_version_in_the_bad_message_becomes_zero(directory, version):
    post = only_post(run(FakeSeat(), example_body("route_request", state_version=version, payload={}), STAY, directory=directory))
    assert parse_message(post.content).state_version == 0


def test_the_name_in_a_retry_key_ignores_case(directory):
    retries = RetryTracker(max_retries=0)
    text = _bad(message_id=str(uuid.uuid4()), payload={})
    assert isinstance(run(FakeSeat(), text, "Plan My Stay Stay Finder", directory=directory, retries=retries), tuple)
    assert isinstance(run(FakeSeat(), text, "PLAN MY STAY STAY FINDER", directory=directory, retries=retries), NoReply)
