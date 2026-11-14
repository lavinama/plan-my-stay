"""The real BAND wiring: a LangGraph graph with one non-LLM node around `process_incoming`.

Everything here was read from band-sdk 9.2.1 (see the notes on each helper); the SDK is imported
inside functions, so importing this module needs neither band-sdk nor langgraph.

How a turn runs (`band.adapters.langgraph.LangGraphAdapter.on_turn`):
  * `graph_factory(band_tools)` is called once per turn with that room's tools. The graph for
    this adapter has the default tool list: band_send_message, band_send_event, band_no_reply,
    band_add_participant, band_remove_participant, band_lookup_peers, band_get_participants,
    band_create_chatroom (memory, contacts, task and file tools are off unless enabled).
  * The graph input is `{"messages": [...]}` and the LAST message is the current input (see
    `inputs.py` for its format). The room id arrives as `config["configurable"]["thread_id"]`.
  * A turn is complete only when a send succeeded or `band_no_reply` was called. Otherwise the
    SDK reports a missing reply and marks the message failed, without retrying it.
  * `band_send_message` never raises for a bad mention or blank body: it returns an error
    message (a ToolMessage with status "error"). So every send's status is checked here.
  * If the graph itself raises, the SDK posts a generic failure event and re-raises; this graph
    only lets host-control errors (room stopped, cleanup) escape.

UNVERIFIED (nothing here was run against live BAND; every test is offline):
  * that a live delivery has the shape the SDK source describes (see inputs.py);
  * that agents registered under one account can add each other to a room (band_add_participant /
    the REST add-participant call) without contact requests, which docs/PLAN.md also lists as open;
  * how the SDK retries a turn whose graph raised (`SessionConfig.max_message_retries` defaults to 1;
    this graph is written never to raise, so the case should not arise);
  * `Agent.run()` / `start()` connecting and serving: build_agent only constructs the Agent.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plan_my_stay.band_adapter.core import NoReply, Post, RetryTracker, process_incoming
from plan_my_stay.band_adapter.directory import SeatDirectory
from plan_my_stay.band_adapter.inputs import extract_inputs
from plan_my_stay.enums import Seat
from plan_my_stay.seat import Seat as SeatHandler

logger = logging.getLogger(__name__)

SEND_MESSAGE = "band_send_message"  # args: content (str), mentions (list of handles, names or ids)
SEND_EVENT = "band_send_event"  # args: content (str), message_type ("error" among others); needs no mention
NO_REPLY = "band_no_reply"  # args: reason (optional, kept in local logs only); ends the turn without a post
DEFAULT_CONFIG_PATH = Path("agent_config.yaml")
_DETAIL_LIMIT = 300


class BandSetupError(RuntimeError):
    """The SDK did not give the graph what the adapter needs. Raised when the graph is built, never mid-turn."""


@dataclass(frozen=True)
class _Tools:
    send: Any
    no_reply: Any
    event: Any | None = None

    @classmethod
    def from_band_tools(cls, band_tools: Sequence[Any]) -> "_Tools":
        by_name = {tool.name: tool for tool in band_tools}
        for required in (SEND_MESSAGE, NO_REPLY):
            if required not in by_name:
                raise BandSetupError(
                    f"the BAND SDK gave the graph no {required} tool (it gave: {sorted(by_name)}); "
                    "a turn cannot end without it, so this SDK version needs a look"
                )
        return cls(send=by_name[SEND_MESSAGE], no_reply=by_name[NO_REPLY], event=by_name.get(SEND_EVENT))


@dataclass(frozen=True)
class _Outcome:
    ok: bool
    detail: str


def _outcome(result: Any) -> _Outcome:
    """Did a tool call succeed? A LangChain ToolMessage says so in `status`; a plain return value is judged by its shape."""
    status = getattr(result, "status", None)
    content = getattr(result, "content", result)
    if status is not None:
        ok = status == "success"
    elif isinstance(result, Mapping):
        ok = result.get("ok") is not False
    else:
        ok = result is not None and not isinstance(result, str)  # the SDK returns an error STRING on failure
    return _Outcome(ok, str(content)[:_DETAIL_LIMIT])


async def _call(tool: Any, args: dict[str, Any]) -> _Outcome:
    """Call a band tool the way LangGraph does (a ToolCall), so the result carries a success/error status."""
    from band.core.exceptions import HOST_CONTROL_ERRORS  # noqa: PLC0415 - lazy: no SDK needed to import this module

    try:
        result = await tool.ainvoke({"name": tool.name, "args": args, "id": f"seat-{uuid.uuid4().hex[:12]}", "type": "tool_call"})
    except HOST_CONTROL_ERRORS:
        raise  # the room was stopped or is shutting down: the host must see it
    except Exception as exc:
        logger.exception("%s raised instead of returning an error", tool.name)
        return _Outcome(False, type(exc).__name__)
    return _outcome(result)


@dataclass(frozen=True)
class _TurnContext:
    seat: SeatHandler
    own_seat: Seat
    directory: SeatDirectory
    retries: RetryTracker
    tools: _Tools
    handle_lock: asyncio.Lock
    room_id: str


async def _decline(ctx: _TurnContext, reason: str) -> None:
    outcome = await _call(ctx.tools.no_reply, {"reason": reason})
    if not outcome.ok:
        logger.error("seat %s room %s: band_no_reply failed: %s", ctx.own_seat.value, ctx.room_id, outcome.detail)


async def _report_failures(ctx: _TurnContext, failures: Sequence[tuple[Post, str]]) -> None:
    """Make a lost message visible in the room as an `error` event (names and reasons, never the message body)."""
    if ctx.tools.event is None:
        return
    lines = [f"to {', '.join(post.mentions)}: {detail}" for post, detail in failures]
    content = f"{ctx.directory.name_of(ctx.own_seat)} could not post {len(failures)} message(s): " + "; ".join(lines)
    outcome = await _call(ctx.tools.event, {"content": content, "message_type": "error"})
    if not outcome.ok:
        logger.error("seat %s room %s: could not post the failure event: %s", ctx.own_seat.value, ctx.room_id, outcome.detail)


async def _deliver(ctx: _TurnContext, posts: Sequence[Post]) -> None:
    sent = 0
    failures: list[tuple[Post, str]] = []
    for post in posts:
        # UNVERIFIED: BAND documents no body length limit, and the SDK enforces none on band_send_message
        # (its only cap, MAX_SEND_CONTENT_BYTES, is for files). The platform may still refuse a long body: a
        # refusal shows up here as a failed outcome with its size in the log. scripts/band_roundtrip.py probes this.
        # UNVERIFIED: a mention resolves only if it equals a room participant's handle, name or id; whether the
        # `name` BAND reports for a seat is exactly its band_seats.yaml display name is checked only against a live room.
        outcome = await _call(ctx.tools.send, {"content": post.content, "mentions": list(post.mentions)})
        if outcome.ok:
            sent += 1
            continue
        failures.append((post, outcome.detail))
        logger.error(
            "seat %s room %s: could not post a %d-byte message to %s: %s",
            ctx.own_seat.value, ctx.room_id, len(post.content.encode("utf-8")), list(post.mentions), outcome.detail,
        )
    if failures:
        await _report_failures(ctx, failures)
    if sent == 0:
        await _decline(ctx, "no message could be posted")  # never leave the turn silent


async def _run_turn(ctx: _TurnContext, input_text: str) -> None:
    inputs = extract_inputs(input_text)
    if not inputs:
        logger.error(
            "seat %s room %s: unrecognised input format (%d chars); ending the turn without a reply",
            ctx.own_seat.value, ctx.room_id, len(input_text),
        )
        await _decline(ctx, "unrecognised input format")
        return
    posts: list[Post] = []
    reasons: list[str] = []
    for item in inputs:
        # Seats are synchronous and may block on HTTP, so run them off the event loop. One at a time:
        # a seat's per-trip state is plain Python, and two rooms must not mutate it concurrently.
        async with ctx.handle_lock:
            result = await asyncio.to_thread(
                process_incoming, ctx.seat, item.content, item.sender_name,
                own_seat=ctx.own_seat, directory=ctx.directory, retries=ctx.retries,
            )
        if isinstance(result, NoReply):
            reasons.append(result.reason)
        else:
            posts.extend(result)
    if posts:
        await _deliver(ctx, posts)
    else:
        await _decline(ctx, "; ".join(r for r in reasons if r) or "nothing to say")


def _last_text(state: Mapping[str, Any]) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""
    content = messages[-1].content
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content if isinstance(b, (str, dict)))
    return str(content)


def make_graph_factory(
    seat: SeatHandler, *, own_seat: Seat, directory: SeatDirectory, retries: RetryTracker
) -> Callable[[list[Any]], Any]:
    """The `graph_factory` for LangGraphAdapter: one graph, one non-LLM node, built from each turn's room tools."""
    handle_lock = asyncio.Lock()

    def graph_factory(band_tools: list[Any]) -> Any:
        from langchain_core.runnables import RunnableConfig  # noqa: PLC0415 - lazy: keeps the SDK optional
        from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: PLC0415

        tools = _Tools.from_band_tools(band_tools)

        async def turn(state: MessagesState, config: RunnableConfig) -> dict[str, list[Any]]:
            room_id = str((config.get("configurable") or {}).get("thread_id", "unknown"))
            ctx = _TurnContext(seat, own_seat, directory, retries, tools, handle_lock, room_id)
            await _run_turn(ctx, _last_text(state))
            return {"messages": []}

        builder = StateGraph(MessagesState)
        builder.add_node("turn", turn)
        builder.add_edge(START, "turn")
        builder.add_edge("turn", END)
        return builder.compile()

    return graph_factory


def _own_seat(seat_name: str) -> Seat:
    try:
        return Seat(seat_name)
    except ValueError:
        raise ValueError(f"unknown seat {seat_name!r}; the seats are {', '.join(s.value for s in Seat)}") from None


def build_adapter(
    seat_name: str,
    seat: SeatHandler,
    *,
    directory: SeatDirectory,
    retries: RetryTracker | None = None,
    emit_tool_events: bool = False,
) -> Any:
    """The LangGraphAdapter for a seat. `seat_name` is one of the six seat names (also its agent_config.yaml key).

    By default the SDK echoes every tool call into the room as `tool_call` / `tool_result` events
    (`Emit.TOOL_CALLS`), which would repeat each message body, with its args and result, next to
    the message itself. That is noise for a plain-code seat, so it is off unless `emit_tool_events`.
    """
    from band.adapters import LangGraphAdapter  # noqa: PLC0415 - lazy: keeps the SDK optional
    from band.core.types import Emit  # noqa: PLC0415

    factory = make_graph_factory(seat, own_seat=_own_seat(seat_name), directory=directory, retries=retries or RetryTracker())
    return LangGraphAdapter(graph_factory=factory, emit=(Emit.TOOL_CALLS,) if emit_tool_events else ())


def build_agent(
    seat_name: str,
    seat: SeatHandler,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    directory: SeatDirectory,
    retries: RetryTracker | None = None,
    emit_tool_events: bool = False,
) -> Any:
    """A band `Agent` for the seat, with credentials read from `config_path` under the key `seat_name`. Does not connect."""
    from band import Agent  # noqa: PLC0415 - lazy: keeps the SDK optional

    adapter = build_adapter(seat_name, seat, directory=directory, retries=retries, emit_tool_events=emit_tool_events)
    return Agent.from_config(seat_name, adapter=adapter, config_path=config_path)


async def run_seat(
    seat_name: str,
    seat: SeatHandler,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    directory: SeatDirectory,
    retries: RetryTracker | None = None,
    emit_tool_events: bool = False,
    log_level: int | None = logging.INFO,
) -> None:
    """Connect the seat to BAND and serve until stopped (Ctrl+C). `log_level=None` leaves logging to the caller."""
    if log_level is not None:
        from band import configure_logging  # noqa: PLC0415 - lazy: keeps the SDK optional

        # band's configure_logging sets only the `band` logger to `log_level` (root stays at WARNING),
        # so name ours too or the outgoing-size lines would not show.
        configure_logging(log_level, extra_loggers={"plan_my_stay": log_level})
    agent = build_agent(
        seat_name, seat, config_path=config_path, directory=directory, retries=retries, emit_tool_events=emit_tool_events
    )
    logger.info("seat %s: connecting to BAND", seat_name)
    await agent.run()
