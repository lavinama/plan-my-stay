"""Live smoke test of the BAND wiring: answers the three open BAND questions in docs/PLAN.md.

    .venv/bin/python scripts/band_roundtrip.py [--config agent_config.yaml] [--seats band_seats.yaml]
                                               [--probe-sizes 8000,16000,64000] [--timeout 90]

It needs real keys in agent_config.yaml for all six seats (copy agent_config.example.yaml) and
network access, so it cannot run in CI. It prints what it observed and never prints a key.

What it checks, in this order:
  A. Are all six seats registered under ONE account? It asks BAND who each key belongs to
     (`get_agent_me`) and compares the owner, and the registered name against band_seats.yaml.
  B. Does an @mention round trip carry a validated message? Concierge posts a `request` to Routing
     in a new room; Routing's adapter parses it and answers with a validated `error` message that
     carries the digest of what it parsed (code `probe_ack`); Concierge's adapter receives it.
  C. Do a full `request`, `proposal` and merged `plan_for_review` arrive intact? The digest of
     what Routing parsed must equal the digest of what was sent. BAND documents no length limit,
     so `--probe-sizes` (optional) also sends valid `error` messages padded to those body sizes
     in bytes (a whole message, because seats never exchange prose); the first size that is
     refused, truncated or never answered is the limit.

It creates one room and leaves it in place (its id is printed). The two probe seats answer only
with `probe_ack` messages, so nothing loops.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plan_my_stay.band_adapter import SeatDirectory, SeatDirectoryError, build_agent
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import Message, build_message, parse_message, serialize
from plan_my_stay.messages.examples import valid_examples
from plan_my_stay.messages.payloads import ErrorPayload
from plan_my_stay.seat import Outgoing

logger = logging.getLogger("band_roundtrip")

PROBE_ACK = "probe_ack"
PROBE_EXAMPLES = ("request", "proposal", "plan_for_review")  # the plan_for_review example is the largest message type
MIN_PROBE_BYTES = 1000  # below this a padded probe is no bigger than the examples
MAX_PROBE_BYTES = 2_000_000
SETTLE_SECONDS = 3.0  # let the receiving seat's runtime learn about the new room before the first mention

Send = Callable[[str], Awaitable[None]]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_probes(sender: Seat = Seat.CONCIERGE, receiver: Seat = Seat.ROUTING) -> dict[str, Message]:
    """The example messages that carry the most data, re-addressed to the probe pair (fresh ids, still validated)."""
    probes: dict[str, Message] = {}
    for name in PROBE_EXAMPLES:
        raw = copy.deepcopy(valid_examples()[name])
        raw.update({"from": sender.value, "to": [receiver.value], "message_id": str(uuid.uuid4())})
        probes[name] = parse_message(json.dumps(raw))
    return probes


def padded_probe(size: int, sender: Seat = Seat.CONCIERGE, receiver: Seat = Seat.ROUTING) -> Message:
    """A valid `error` message whose serialized body is exactly `size` bytes (ASCII padding in `fields`, which is unbounded)."""

    def build(padding: str) -> Message:
        return build_message(
            type=MessageType.ERROR, sender=sender, to=[receiver], trip_id="size-probe", user_id="size-probe", state_version=0,
            summary="size probe", payload=ErrorPayload(code="probe_pad", fields=(padding,)),
        )

    base = len(serialize(build("")).encode("utf-8"))
    if size < base:
        raise ValueError(f"a padded probe needs at least {base} bytes")
    probe = build("x" * (size - base))
    assert len(serialize(probe).encode("utf-8")) == size  # ASCII only, so characters equal bytes
    return probe


class AckSeat:
    """The receiving seat: answers every message with a digest of what it parsed, so the sender can compare."""

    def __init__(self, own: Seat) -> None:
        self._own = own

    def handle(self, message: Message) -> Sequence[Outgoing]:
        canonical = serialize(message)
        ack = build_message(
            type=MessageType.ERROR,
            sender=self._own,
            to=[message.sender],
            trip_id=message.trip_id,
            user_id=message.user_id,
            state_version=message.state_version,
            summary=f"probe ack for {message.type.value}",
            reply_to=message.message_id,
            payload=ErrorPayload(
                code=PROBE_ACK,
                bad_message_id=message.message_id,
                fields=(f"sha256:{digest(canonical)}", f"bytes:{len(canonical.encode('utf-8'))}"),
            ),
        )
        return [ack]


class CaptureSeat:
    """The sending seat's inbox: hands everything it receives to the main task and never answers."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[tuple[str, Any]]") -> None:
        self._loop = loop
        self._queue = queue

    def handle(self, message: Message) -> Sequence[Outgoing]:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, ("message", message))  # runs in a worker thread
        return []


@dataclass(frozen=True)
class Observation:
    label: str
    ok: bool
    detail: str


def parse_ack_fields(fields: Sequence[str]) -> tuple[str | None, int | None]:
    """(sha256, bytes) from an ack's `fields`."""
    values = dict(f.split(":", 1) for f in fields if ":" in f)
    size = values.get("bytes", "")
    return values.get("sha256"), int(size) if size.isdigit() else None


async def _next(queue: "asyncio.Queue[tuple[str, Any]]", deadline: float) -> tuple[str, Any] | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        return await asyncio.wait_for(queue.get(), remaining)
    except TimeoutError:
        return None


async def probe_message(send: Send, queue: "asyncio.Queue[tuple[str, Any]]", name: str, probe: Message, timeout: float) -> Observation:
    """Send one validated message and wait for Routing's digest of what it parsed."""
    body = serialize(probe)
    size = len(body.encode("utf-8"))
    label = f"{name} ({size} bytes)"
    started = time.monotonic()
    try:
        await send(body)
    except Exception as exc:  # reported, not raised: a refused send is exactly what this script exists to observe
        return Observation(label, False, f"BAND refused the send: {exc}")
    deadline = started + timeout
    while (item := await _next(queue, deadline)) is not None:
        kind, value = item
        if kind != "message" or value.type is not MessageType.ERROR:
            continue
        if value.payload.code != PROBE_ACK:
            return Observation(label, False, f"the receiver rejected it: {value.payload.code} {list(value.payload.fields)}")
        if value.reply_to != probe.message_id:
            continue
        seen_sha, seen_bytes = parse_ack_fields(value.payload.fields)
        took = time.monotonic() - started
        if seen_sha == digest(body):
            return Observation(label, True, f"arrived intact, answered in {took:.1f}s")
        return Observation(label, False, f"arrived DIFFERENT: sent sha256 {digest(body)[:12]} ({size} bytes), received {str(seen_sha)[:12]} ({seen_bytes} bytes)")
    return Observation(label, False, f"no answer within {timeout:.0f}s")


# --- live part: needs the SDK, keys and network -----------------------------------------------------


def _api_error(exc: Exception) -> str:
    """A short description of a BAND API error. Never `str(exc)`: the SDK's error text includes response headers."""
    body = getattr(exc, "body", None)
    message = getattr(getattr(body, "error", None), "message", None)
    status = getattr(exc, "status_code", None)
    return f"HTTP {status}: {message}" if message else f"HTTP {status}" if status else type(exc).__name__


async def check_registration(config_path: Path, directory: SeatDirectory, rest_url: str) -> tuple[list[Observation], dict[Seat, Any]]:
    """Check A: who owns each seat's key, and does its registered name match band_seats.yaml?"""
    from band.client.rest import DEFAULT_REQUEST_OPTIONS, aclose_rest_client  # noqa: PLC0415 - lazy: --help needs no SDK
    from band.config.loader import load_agent_config  # noqa: PLC0415
    from band_rest import AsyncRestClient  # noqa: PLC0415
    from band_rest.core.api_error import ApiError  # noqa: PLC0415

    rows: list[Observation] = []
    identities: dict[Seat, Any] = {}
    for seat in Seat:
        try:
            _agent_id, api_key = load_agent_config(seat.value, config_path=config_path)
        except ValueError as exc:
            rows.append(Observation(f"{seat.value}", False, f"not configured: {exc}"))
            continue
        client = AsyncRestClient(api_key=api_key, base_url=rest_url.rstrip("/"))
        try:
            me = (await client.agent_api_identity.get_agent_me(request_options=DEFAULT_REQUEST_OPTIONS)).data
        except ApiError as exc:
            rows.append(Observation(f"{seat.value}", False, f"BAND rejected this key ({_api_error(exc)})"))
            continue
        except Exception as exc:  # no network, DNS, TLS: say what kind, never the exception text
            rows.append(Observation(f"{seat.value}", False, f"could not reach BAND ({type(exc).__name__})"))
            continue
        finally:
            await aclose_rest_client(client)
        identities[seat] = me
        name_ok = directory.seat_of(me.name) is seat
        described = len(me.description or "") >= 10
        detail = f"registered as {me.name!r} ({me.handle}), owner ...{me.owner_uuid[-6:]}"
        if not name_ok:
            detail += f"; band_seats.yaml expects {directory.name_of(seat)!r}: FIX ONE OF THEM"
        if not described:
            detail += "; description shorter than 10 characters"
        rows.append(Observation(f"{seat.value}", name_ok and described, detail))
    return rows, identities


def same_account(identities: dict[Seat, Any]) -> Observation:
    owners = {me.owner_uuid for me in identities.values()}
    if len(identities) < len(Seat):
        return Observation("six seats under one account", False, f"only {len(identities)} of {len(Seat)} seats could be checked")
    if len(owners) == 1:
        return Observation("six seats under one account", True, "yes: all six keys belong to one owner")
    return Observation("six seats under one account", False, f"no: {len(owners)} different owners")


async def run_round_trip(config_path: Path, directory: SeatDirectory, rest_url: str, identities: dict[Seat, Any], timeout: float, sizes: Sequence[int]) -> tuple[list[Observation], str]:
    """Checks B and C: two real adapters in this process, one new room, the probes."""
    from band.client.rest import DEFAULT_REQUEST_OPTIONS, aclose_rest_client  # noqa: PLC0415
    from band.config.loader import load_agent_config  # noqa: PLC0415
    from band_rest import AsyncRestClient, ChatMessageRequest, ChatMessageRequestMentionsItem, ChatRoomRequest, ParticipantRequest  # noqa: PLC0415

    sender, receiver = Seat.CONCIERGE, Seat.ROUTING
    target = identities[receiver]
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    receiver_agent = build_agent(receiver.value, AckSeat(receiver), config_path=config_path, directory=directory)
    sender_agent = build_agent(sender.value, CaptureSeat(loop, queue), config_path=config_path, directory=directory)
    _id, api_key = load_agent_config(sender.value, config_path=config_path)
    client = AsyncRestClient(api_key=api_key, base_url=rest_url.rstrip("/"))
    results: list[Observation] = []
    room_id = "(not created)"
    try:
        await receiver_agent.start()
        await sender_agent.start()
        room = await client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest(), request_options=DEFAULT_REQUEST_OPTIONS)
        room_id = room.data.id
        await client.agent_api_participants.add_agent_chat_participant(
            chat_id=room_id, participant=ParticipantRequest(participant_id=target.id), request_options=DEFAULT_REQUEST_OPTIONS
        )
        await asyncio.sleep(SETTLE_SECONDS)

        async def send(body: str) -> None:
            await client.agent_api_messages.create_agent_chat_message(
                chat_id=room_id,
                message=ChatMessageRequest(content=body, mentions=[ChatMessageRequestMentionsItem(id=target.id, handle=target.handle)]),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )

        for name, probe in build_probes(sender, receiver).items():
            results.append(await probe_message(send, queue, name, probe, timeout))
        for size in sizes:
            results.append(await probe_message(send, queue, "padded error", padded_probe(size, sender, receiver), timeout))
    except Exception as exc:
        results.append(Observation("round trip setup", False, f"{type(exc).__name__}: {_api_error(exc) if hasattr(exc, 'status_code') else exc}"))
    finally:
        for agent in (sender_agent, receiver_agent):
            try:
                await agent.stop(timeout=10)
            except Exception:
                logger.exception("could not stop an agent cleanly")
        await aclose_rest_client(client)
    return results, room_id


def print_report(title: str, rows: Sequence[Observation]) -> None:
    print(f"\n{title}")
    for row in rows:
        print(f"  [{'ok' if row.ok else 'FAIL'}] {row.label}: {row.detail}")


def _sizes(text: str) -> list[int]:
    try:
        sizes = [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("--probe-sizes is a comma-separated list of body sizes in bytes") from None
    if any(not MIN_PROBE_BYTES <= size <= MAX_PROBE_BYTES for size in sizes):
        raise argparse.ArgumentTypeError(f"each probe size must be between {MIN_PROBE_BYTES:,} and {MAX_PROBE_BYTES:,} bytes")
    return sizes


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live smoke test of the BAND wiring (needs real keys; see the module docstring).")
    parser.add_argument("--config", type=Path, default=Path("agent_config.yaml"), help="API keys, one entry per seat (git-ignored)")
    parser.add_argument("--seats", type=Path, default=Path("band_seats.yaml"), help="seat -> BAND display name")
    parser.add_argument("--rest-url", default=None, help="BAND REST URL (default: the SDK's, BAND_REST_URL overrides)")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for each answer (default 90)")
    parser.add_argument("--probe-sizes", type=_sizes, default=[], help="also send valid messages padded to these body sizes in bytes, e.g. 8000,16000,64000")
    parser.add_argument("--skip-round-trip", action="store_true", help="only check registration (check A)")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    from band.config.settings import PlatformSettings  # noqa: PLC0415

    directory = SeatDirectory.load(args.seats)
    rest_url = args.rest_url or PlatformSettings().BAND_REST_URL
    rows, identities = await check_registration(args.config, directory, rest_url)
    rows.append(same_account(identities))
    print_report("A. Registration (all six seats under one account?)", rows)
    ok = all(r.ok for r in rows)
    if args.skip_round_trip:
        return 0 if ok else 1
    if not {Seat.CONCIERGE, Seat.ROUTING} <= set(identities):
        print("\nSkipping the round trip: the Concierge and Routing keys must both work.")
        return 1
    results, room_id = await run_round_trip(args.config, directory, rest_url, identities, args.timeout, args.probe_sizes)
    print_report(f"B and C. Round trip Concierge -> Routing and back (room {room_id})", results)
    intact = [r for r in results if r.ok]
    print(f"\n{len(intact)} of {len(results)} probes arrived intact.")
    return 0 if ok and all(r.ok for r in results) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(_run(args))
    except (FileNotFoundError, SeatDirectoryError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
