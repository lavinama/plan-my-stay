"""sdk.py with the SDK's real tools and adapter (offline): the SDK's own turn rule, build_adapter, build_agent, run_seat."""

from typing import Any

import pytest

pytest.importorskip("band")
pytest.importorskip("langgraph")

from band.adapters import LangGraphAdapter  # noqa: E402

from plan_my_stay.band_adapter import sdk  # noqa: E402
from plan_my_stay.band_adapter.directory import SeatDirectory  # noqa: E402
from plan_my_stay.band_adapter.inputs import extract_inputs  # noqa: E402
from plan_my_stay.enums import MessageType, Seat  # noqa: E402
from plan_my_stay.messages import parse_message, serialize  # noqa: E402

from .conftest import NAMES, example_body, route_reply_to, route_request_for_routing  # noqa: E402,F401
from .sdk_helpers import PERSON, ROOM, STAY, ScriptedSeat, StubRest, platform_message, real_turn, turn_text  # noqa: E402,F401


def test_real_adapter_turn_with_a_reply_is_complete_and_mentions_resolve_by_display_name(directory):
    request = route_request_for_routing()
    reply = route_reply_to(request)
    tools, rest = real_turn(directory, ScriptedSeat([reply]), [platform_message(serialize(request))])
    assert tools.turn.complete
    (room, posted), = rest.messages
    assert room == ROOM and posted.content == serialize(reply)
    assert [m.id for m in posted.mentions] == ["p-stay"]
    assert rest.events == []  # the SDK does not echo our send into the room as tool_call/tool_result events


def test_tool_call_events_come_back_when_asked_for(directory):
    request = route_request_for_routing()
    tools, rest = real_turn(directory, ScriptedSeat([route_reply_to(request)]), [platform_message(serialize(request))], emit_tool_events=True)
    assert tools.turn.complete
    assert {e.message_type for _, e in rest.events} == {"tool_call", "tool_result"}


def test_real_adapter_turn_with_nothing_to_say_is_still_complete_by_the_sdks_rule(directory):
    tools, rest = real_turn(directory, ScriptedSeat([]), [platform_message(serialize(route_request_for_routing()))])
    assert tools.turn.complete
    assert rest.messages == []


def test_real_adapter_turn_for_a_crashing_seat_still_answers_with_an_internal_error(directory):
    request = route_request_for_routing()
    tools, rest = real_turn(directory, ScriptedSeat(RuntimeError("kaboom")), [platform_message(serialize(request))])
    assert tools.turn.complete
    posted = parse_message(rest.messages[0][1].content)
    assert posted.type is MessageType.ERROR and posted.payload.code == "internal_error"


def test_real_adapter_turn_whose_send_fails_is_still_complete_not_a_failed_silent_turn(directory):
    # the Stay seat's display name is not a participant of this room: the real send tool returns an error
    # string (it never raises), and the adapter must still end the turn instead of leaving it silent
    absent = SeatDirectory({**NAMES, Seat.STAY: "Plan My Stay Absent Finder"})
    request = route_request_for_routing()
    tools, rest = real_turn(absent, ScriptedSeat([route_reply_to(request)]), [platform_message(serialize(request))])
    assert tools.turn.complete
    assert rest.messages == []
    assert [e.message_type for _, e in rest.events if e.message_type == "error"] == ["error"]


def test_the_extractor_reads_exactly_what_the_installed_sdk_formats():
    text = turn_text(platform_message("@x hello", sender_name=PERSON, sender_type="User", sender_id="u1", message_id="m9"))
    (item,) = extract_inputs(text)
    assert (item.sender_name, item.content, item.message_id, item.sender_id, item.sender_type) == (PERSON, "@x hello", "m9", "u1", "User")


# --- build_adapter / build_agent / run_seat ---------------------------------------------------


def test_build_adapter_returns_a_langgraph_adapter_that_judges_turns(directory):
    adapter = sdk.build_adapter("routing", ScriptedSeat(), directory=directory)
    assert isinstance(adapter, LangGraphAdapter)
    assert adapter.judges_turns


def test_build_adapter_rejects_a_name_that_is_not_one_of_the_six_seats(directory):
    with pytest.raises(ValueError, match="chef"):
        sdk.build_adapter("chef", ScriptedSeat(), directory=directory)


def test_build_agent_reads_the_seat_entry_from_the_config_without_connecting(directory, tmp_path):
    config = tmp_path / "agent_config.yaml"
    config.write_text('routing:\n  agent_id: "00000000-0000-0000-0000-000000000001"\n  api_key: "test-key-not-real"\n', encoding="utf-8")
    agent = sdk.build_agent("routing", ScriptedSeat(), config_path=config, directory=directory)
    assert not agent.is_running
    assert "test-key-not-real" not in repr(agent)


def test_build_agent_reports_a_missing_seat_entry_by_name(directory, tmp_path):
    config = tmp_path / "agent_config.yaml"
    config.write_text('gym:\n  agent_id: "x"\n  api_key: "y"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="routing"):
        sdk.build_agent("routing", ScriptedSeat(), config_path=config, directory=directory)


def test_build_agent_reports_a_missing_config_file(directory, tmp_path):
    with pytest.raises(FileNotFoundError, match="agent_config"):
        sdk.build_agent("routing", ScriptedSeat(), config_path=tmp_path / "agent_config.yaml", directory=directory)


async def test_run_seat_builds_the_agent_and_runs_it(directory, monkeypatch, tmp_path):
    ran: list[str] = []

    class FakeAgent:
        async def run(self) -> None:
            ran.append("run")

    built: dict[str, Any] = {}

    def fake_build(seat_name, seat, *, config_path, directory, retries=None, emit_tool_events=False):
        built.update(seat_name=seat_name, config_path=config_path)
        return FakeAgent()

    monkeypatch.setattr(sdk, "build_agent", fake_build)
    await sdk.run_seat("gym", ScriptedSeat(), config_path=tmp_path / "c.yaml", directory=directory, log_level=None)
    assert ran == ["run"]
    assert built["seat_name"] == "gym"
