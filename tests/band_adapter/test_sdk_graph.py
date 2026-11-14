"""sdk.py: the graph node against fake band tools (real StructuredTools, no network)."""

import asyncio
import logging
import threading
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("band")
pytest.importorskip("langgraph")

from plan_my_stay.band_adapter import sdk  # noqa: E402
from plan_my_stay.enums import MessageType, Seat  # noqa: E402
from plan_my_stay.messages import parse_message, serialize  # noqa: E402

from .conftest import NAMES, example_body, route_reply_to, route_request_for_routing  # noqa: E402
from .sdk_helpers import (  # noqa: E402,F401
    PERSON, ROOM, STAY, FakeBand, ScriptedSeat, StubRest, make_factory, platform_message, real_turn, run_graph, turn_text,
)


# --- the graph node ---------------------------------------------------------------------------


async def test_graph_posts_the_reply_with_mentions_from_to_and_does_not_also_decline(directory):
    request = route_request_for_routing()
    reply = route_reply_to(request)
    band = FakeBand()
    await run_graph(make_factory(ScriptedSeat([reply]), directory), band, turn_text(platform_message(serialize(request))))
    assert band.sent == [{"content": serialize(reply), "mentions": [STAY]}]
    assert band.no_replies == []


async def test_graph_understands_a_mention_prefixed_body_as_the_platform_delivers_it(directory):
    request = route_request_for_routing()
    seat = ScriptedSeat([])
    body = f"@mario/plan-my-stay-routing {serialize(request)}"
    await run_graph(make_factory(seat, directory), FakeBand(), turn_text(platform_message(body)))
    assert seat.handled == [request]


async def test_graph_ends_the_turn_with_band_no_reply_when_there_is_nothing_to_say(directory):
    band = FakeBand()
    await run_graph(make_factory(ScriptedSeat([]), directory), band, turn_text(platform_message(serialize(route_request_for_routing()))))
    assert band.sent == []
    assert len(band.no_replies) == 1
    assert band.no_replies[0]


async def test_prose_from_a_person_reaches_handle_text_and_the_answer_mentions_the_person(directory):
    seat = ScriptedSeat()
    band = FakeBand()
    text = turn_text(platform_message("@mario/plan-my-stay-concierge plan my trip", sender_name=PERSON, sender_type="User", sender_id="u1"))
    await run_graph(make_factory(seat, directory, own=Seat.CONCIERGE), band, text)
    assert seat.texts == [(PERSON, "plan my trip")]
    assert band.sent == [{"content": "echo: plan my trip", "mentions": [PERSON]}]


async def test_a_bad_message_gets_one_error_post_to_the_sender(directory):
    band = FakeBand()
    await run_graph(make_factory(ScriptedSeat(), directory), band, turn_text(platform_message("not json")))
    assert len(band.sent) == 1
    assert band.sent[0]["mentions"] == [STAY]
    assert parse_message(band.sent[0]["content"]).payload.code == "invalid_json"


async def test_a_batch_of_inputs_is_answered_in_order_with_one_post_each(directory):
    first = route_request_for_routing()
    reply = route_reply_to(first)
    seat = ScriptedSeat([reply])
    band = FakeBand()
    text = turn_text(platform_message(serialize(first), message_id="m1"), platform_message(serialize(first), message_id="m2"))
    await run_graph(make_factory(seat, directory), band, text)
    assert len(seat.handled) == 2
    assert [s["content"] for s in band.sent] == [serialize(reply)] * 2


async def test_the_sender_id_is_the_mention_when_a_sender_has_no_name(directory):
    band = FakeBand()
    text = turn_text(platform_message("hello", sender_name=None, sender_id="uuid-77"))
    await run_graph(make_factory(ScriptedSeat(), directory), band, text)
    assert band.sent[0]["mentions"] == ["uuid-77"]


async def test_unrecognised_input_is_logged_and_the_turn_still_ends(directory, caplog):
    band = FakeBand()
    with caplog.at_level(logging.ERROR):
        await run_graph(make_factory(ScriptedSeat(), directory), band, "something the adapter never sends")
    assert band.sent == []
    assert len(band.no_replies) == 1
    assert any("unrecognised" in r.getMessage() for r in caplog.records)


async def test_handle_runs_in_a_worker_thread_not_on_the_event_loop(directory):
    seat = ScriptedSeat([])
    loop_thread = threading.get_ident()
    await run_graph(make_factory(seat, directory), FakeBand(), turn_text(platform_message(serialize(route_request_for_routing()))))
    assert seat.thread_ids and seat.thread_ids[0] != loop_thread


async def test_two_turns_never_run_handle_at_the_same_time(directory):
    active = 0
    peak = 0

    class SlowSeat:
        def handle(self, message):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            threading.Event().wait(0.05)
            active -= 1
            return []

    factory = make_factory(SlowSeat(), directory)
    text = turn_text(platform_message(serialize(route_request_for_routing())))
    await asyncio.gather(run_graph(factory, FakeBand(), text, room="a"), run_graph(factory, FakeBand(), text, room="b"))
    assert peak == 1




# --- sends that fail never leave the turn silent ----------------------------------------------


async def test_when_every_send_fails_an_error_event_is_posted_and_the_turn_ends_with_no_reply(directory, caplog):
    request = route_request_for_routing()
    band = FakeBand(fail_for=(STAY,))
    with caplog.at_level(logging.ERROR):
        await run_graph(make_factory(ScriptedSeat([route_reply_to(request)]), directory), band, turn_text(platform_message(serialize(request))))
    assert band.sent == []
    assert [e["message_type"] for e in band.events] == ["error"]
    assert STAY in band.events[0]["content"]
    assert serialize(route_reply_to(request)) not in band.events[0]["content"]  # the body is not dumped into the room
    assert len(band.no_replies) == 1
    assert any("could not post" in r.getMessage() for r in caplog.records)


async def test_when_some_sends_fail_the_rest_still_go_out_and_the_failure_is_reported(directory):
    pick = parse_message(example_body("stay_pick"))  # to restaurant, gym, concierge
    reply_ok = route_reply_to(route_request_for_routing())  # to stay
    band = FakeBand(fail_for=(NAMES[Seat.GYM],))
    seat = ScriptedSeat([pick, reply_ok])
    await run_graph(make_factory(seat, directory, own=Seat.STAY), band, turn_text(platform_message(serialize(route_request_for_routing()), sender_name=PERSON, sender_type="User")))
    assert [s["content"] for s in band.sent] == [serialize(reply_ok)]
    assert len(band.events) == 1
    assert band.no_replies == []  # a reply went out, so the turn is complete; declining would contradict it


def test_a_missing_send_tool_fails_loudly_when_the_graph_is_built(directory):
    factory = make_factory(ScriptedSeat(), directory)
    with pytest.raises(sdk.BandSetupError, match="band_send_message"):
        factory(FakeBand(omit=("band_send_message",)).tools())
    with pytest.raises(sdk.BandSetupError, match="band_no_reply"):
        factory(FakeBand(omit=("band_no_reply",)).tools())




# --- tool failures in every shape ------------------------------------------------------------


async def _one_reply_turn(directory, band, *, own=Seat.ROUTING):
    request = route_request_for_routing()
    await run_graph(make_factory(ScriptedSeat([route_reply_to(request)]), directory, own=own), band, turn_text(platform_message(serialize(request))))


async def test_a_send_that_raises_instead_of_returning_an_error_is_logged_and_the_turn_still_ends(directory, caplog):
    band = FakeBand(explode=True)
    with caplog.at_level(logging.ERROR):
        await _one_reply_turn(directory, band)
    assert len(band.no_replies) == 1
    assert any("raised instead of returning an error" in r.getMessage() and r.exc_info for r in caplog.records)


async def test_host_control_errors_are_never_swallowed(directory):
    from band.core.exceptions import RoomExecutionStoppedError

    band = FakeBand()

    async def stopped(content: str, mentions: list[str]) -> dict[str, str]:
        raise RoomExecutionStoppedError(ROOM)

    band._send = stopped  # type: ignore[method-assign]
    with pytest.raises(RoomExecutionStoppedError):
        await _one_reply_turn(directory, band)


async def test_a_failing_no_reply_is_logged_not_raised(directory, caplog):
    band = FakeBand(fail_no_reply=True)
    with caplog.at_level(logging.ERROR):
        await run_graph(make_factory(ScriptedSeat([]), directory), band, turn_text(platform_message(serialize(route_request_for_routing()))))
    assert any("band_no_reply failed" in r.getMessage() for r in caplog.records)


async def test_a_failing_failure_event_is_logged_and_the_turn_still_ends(directory, caplog):
    band = FakeBand(fail_for=(STAY,), fail_event=True)
    with caplog.at_level(logging.ERROR):
        await _one_reply_turn(directory, band)
    assert len(band.no_replies) == 1
    assert any("could not post the failure event" in r.getMessage() for r in caplog.records)


async def test_without_an_event_tool_a_failed_send_still_ends_the_turn(directory):
    band = FakeBand(fail_for=(STAY,), omit=("band_send_event",))
    await _one_reply_turn(directory, band)
    assert band.events == [] and len(band.no_replies) == 1




# --- small helpers ---------------------------------------------------------------------------


def test_outcome_reads_plain_values_by_their_shape():
    assert sdk._outcome({"id": "m1"}).ok
    assert not sdk._outcome({"ok": False, "error": "x"}).ok
    assert not sdk._outcome("Error executing band_send_message: nope").ok
    assert not sdk._outcome(None).ok
    assert sdk._outcome(SimpleNamespace(status="success", content="fine")).ok
    assert not sdk._outcome(SimpleNamespace(status="error", content="bad")).ok
    assert len(sdk._outcome("x" * 5000).detail) == 300


def test_last_text_reads_strings_content_blocks_and_empty_state():
    assert sdk._last_text({"messages": []}) == ""
    assert sdk._last_text({}) == ""
    assert sdk._last_text({"messages": [SimpleNamespace(content="hello")]}) == "hello"
    blocks = [{"type": "text", "text": "he"}, "llo", 7]
    assert sdk._last_text({"messages": [SimpleNamespace(content=blocks)]}) == "hello"
    assert sdk._last_text({"messages": [SimpleNamespace(content=42)]}) == "42"


def test_tool_names_are_the_ones_the_installed_sdk_defines():
    from band.runtime.tools import BandTool

    assert sdk.SEND_MESSAGE == BandTool.SEND_MESSAGE
    assert sdk.SEND_EVENT == BandTool.SEND_EVENT
    assert sdk.NO_REPLY == BandTool.NO_REPLY


async def test_run_seat_sets_up_logging_for_both_band_and_this_package(directory, monkeypatch, tmp_path):
    import band

    configured: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(band, "configure_logging", lambda level, **kw: configured.append((level, kw)))

    class FakeAgent:
        async def run(self) -> None:
            return None

    monkeypatch.setattr(sdk, "build_agent", lambda *a, **k: FakeAgent())
    await sdk.run_seat("gym", ScriptedSeat(), config_path=tmp_path / "c.yaml", directory=directory)
    assert configured == [(logging.INFO, {"extra_loggers": {"plan_my_stay": logging.INFO}})]
