"""Shared fakes for the sdk tests: band tools as the graph receives them, and real AgentTools over a stub REST client."""

import asyncio
import datetime as dt
import threading
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("band")
pytest.importorskip("langgraph")

from band.adapters import LangGraphAdapter  # noqa: E402
from band.core.types import PlatformMessage  # noqa: E402
from band.runtime.formatters import format_current_inputs  # noqa: E402
from band.runtime.tools import AgentTools, NoReplyInput, SendEventInput, SendMessageInput  # noqa: E402
from langchain_core.tools import StructuredTool, ToolException  # noqa: E402

from plan_my_stay.band_adapter import sdk  # noqa: E402
from plan_my_stay.band_adapter.core import RetryTracker  # noqa: E402
from plan_my_stay.band_adapter.directory import SeatDirectory  # noqa: E402
from plan_my_stay.band_adapter.inputs import extract_inputs  # noqa: E402
from plan_my_stay.enums import MessageType, Seat  # noqa: E402
from plan_my_stay.messages import parse_message, serialize  # noqa: E402
from plan_my_stay.seat import TextReply  # noqa: E402

from .conftest import NAMES, example_body, route_reply_to, route_request_for_routing  # noqa: E402

STAY = NAMES[Seat.STAY]
PERSON = "Mario Lavina"
ROOM = "room-1"


def platform_message(content: str, *, sender_name: str | None = STAY, sender_type: str = "Agent",
                     sender_id: str = "agent-1", message_id: str = "m-in") -> PlatformMessage:
    return PlatformMessage(
        id=message_id, room_id=ROOM, content=content, sender_id=sender_id, sender_type=sender_type,
        sender_name=sender_name, message_type="text", metadata={},
        created_at=dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC),
    )


def turn_text(*messages: PlatformMessage) -> str:
    """The text the real adapter hands the graph: the SDK's own formatter, so a format change breaks this test."""
    return format_current_inputs(list(messages))


class ScriptedSeat:
    def __init__(self, replies=()) -> None:
        self.replies = replies
        self.handled: list[Any] = []
        self.texts: list[tuple[str, str]] = []
        self.thread_ids: list[int] = []

    def handle(self, message):
        self.thread_ids.append(threading.get_ident())
        self.handled.append(message)
        if isinstance(self.replies, Exception):
            raise self.replies
        return self.replies

    def handle_text(self, sender, text):
        self.texts.append((sender, text))
        return [TextReply(f"echo: {text}", (sender,))]


class FakeBand:
    """Band tools as the graph receives them (real StructuredTools), recording what the graph does."""

    def __init__(self, fail_for: tuple[str, ...] = (), omit: tuple[str, ...] = (), *, explode: bool = False,
                 fail_event: bool = False, fail_no_reply: bool = False) -> None:
        self.fail_for = set(fail_for)
        self.omit = set(omit)
        self.explode = explode
        self.fail_event = fail_event
        self.fail_no_reply = fail_no_reply
        self.sent: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.no_replies: list[str | None] = []

    async def _send(self, content: str, mentions: list[str]) -> dict[str, str]:
        if self.explode:
            raise RuntimeError("the network fell over")
        if self.fail_for & set(mentions):
            raise ToolException(f"Unknown participant '{sorted(self.fail_for & set(mentions))[0]}'")
        self.sent.append({"content": content, "mentions": mentions})
        return {"id": f"m{len(self.sent)}"}

    async def _event(self, content: str, message_type: str, metadata: dict | None = None) -> dict[str, str]:
        if self.fail_event:
            raise ToolException("event refused")
        self.events.append({"content": content, "message_type": message_type})
        return {"id": "e"}

    async def _no_reply(self, reason: str | None = None) -> dict[str, str]:
        if self.fail_no_reply:
            raise ToolException("no_reply refused")
        self.no_replies.append(reason)
        return {"status": "no_reply"}

    def tools(self) -> list[StructuredTool]:
        specs = [
            ("band_send_message", self._send, SendMessageInput),
            ("band_send_event", self._event, SendEventInput),
            ("band_no_reply", self._no_reply, NoReplyInput),
        ]
        return [
            StructuredTool.from_function(coroutine=fn, name=name, description=name, args_schema=schema, handle_tool_error=True)
            for name, fn, schema in specs
            if name not in self.omit
        ]


def make_factory(seat, directory, *, own=Seat.ROUTING, retries=None):
    return sdk.make_graph_factory(seat, own_seat=own, directory=directory, retries=retries or RetryTracker())


async def run_graph(factory, band: FakeBand, text: str, *, room: str = ROOM) -> None:
    graph = factory(band.tools())
    state = {"messages": [("user", "[System]: participants changed"), ("user", text)]}
    await graph.ainvoke(state, config={"configurable": {"thread_id": room}})




class StubRest:
    """Just enough REST for AgentTools: records posts, returns what the real client's responses expose."""

    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.events: list[Any] = []
        outer = self

        class Messages:
            async def create_agent_chat_message(self, chat_id, message, request_options=None):
                outer.messages.append((chat_id, message))
                return SimpleNamespace(data=SimpleNamespace(model_dump=lambda: {"id": "m1", "recipients": []}))

        class Events:
            async def create_agent_chat_event(self, chat_id, event, request_options=None):
                outer.events.append((chat_id, event))
                return SimpleNamespace(data=SimpleNamespace(model_dump=lambda: {"id": "e1"}))

        self.agent_api_messages = Messages()
        self.agent_api_events = Events()


def real_turn(directory, seat, text_messages, *, own=Seat.ROUTING, emit_tool_events=False):
    """Drive the real LangGraphAdapter.on_turn with real AgentTools; returns (tools, rest)."""
    rest = StubRest()
    participants = [
        {"id": "p-stay", "name": STAY, "handle": "mario/plan-my-stay-stay-finder", "type": "Agent"},
        {"id": "p-person", "name": PERSON, "handle": "mario", "type": "User"},
    ]
    tools = AgentTools(room_id=ROOM, rest=rest, participants=participants, agent_id="agent-me")
    adapter = sdk.build_adapter(own.value, seat, directory=directory, emit_tool_events=emit_tool_events)

    async def go() -> None:
        await adapter.on_started(NAMES[own], "A seat in the Plan My Stay team")
        await adapter.on_turn(text_messages, tools, [], None, None, is_session_bootstrap=True, room_id=ROOM)

    asyncio.run(go())
    return tools, rest
