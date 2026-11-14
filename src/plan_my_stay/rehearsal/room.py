"""An in-process stand-in for the BAND room, so a whole run needs no keys and no network.

Every message crosses the room as JSON text with BAND's decoration ("[Sender]: @handle {json}"),
so a seat sees exactly what it would see on BAND, and a serialization bug shows up here. This is
for rehearsals and CI; the real transport is `band_adapter`.
"""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from plan_my_stay.enums import Seat as SeatName
from plan_my_stay.messages import Message, parse_message, serialize
from plan_my_stay.seat import TextReply


class RoomError(RuntimeError):
    """A seat sent something the room cannot deliver."""


class RoomLoopError(RoomError):
    """The seats kept answering each other past the step cap: a message loop."""


@dataclass(frozen=True)
class LogEntry:
    step: int
    kind: str  # "message" or "text"
    sender: str
    to: tuple[str, ...]
    type: str
    summary: str


class Room:
    def __init__(self, seats: Mapping[SeatName, Any], *, max_steps: int = 300) -> None:
        self._seats = dict(seats)
        self._max_steps = max_steps
        self._queue: deque[tuple[SeatName, Message]] = deque()
        self._events: list[LogEntry] = []
        self._replies: list[TextReply] = []
        self._messages: list[Message] = []
        self._deliveries = 0

    @property
    def messages(self) -> tuple[Message, ...]:
        """Every machine message sent, in order, as the parsed objects (for assertions on what crossed the room)."""
        return tuple(self._messages)

    @property
    def log(self) -> tuple[LogEntry, ...]:
        """Every machine message sent, in order."""
        return tuple(e for e in self._events if e.kind == "message")

    @property
    def replies(self) -> tuple[TextReply, ...]:
        """Everything the seats said to people."""
        return tuple(self._replies)

    def say(self, person: str, text: str, *, to: SeatName = SeatName.CONCIERGE) -> None:
        """A person writes prose to a seat (the Concierge) and the room runs until it is quiet."""
        handler = getattr(self._seat(to), "handle_text", None)
        if handler is None:
            raise RoomError(f"{to.value} does not read prose")
        self._send(to, handler(person, text))
        self._drain()

    def format_transcript(self) -> str:
        """The room as people would read it in Band Desktop."""
        lines = []
        for e in self._events:
            if e.kind == "text":
                lines.append(f"{e.step:>3}  {e.sender} says to {', '.join(e.to)}: {e.summary}")
            else:
                lines.append(f"{e.step:>3}  {e.sender} -> {', '.join(e.to)}  [{e.type}]  {e.summary}")
        return "\n".join(lines)

    def _seat(self, name: SeatName) -> Any:
        try:
            return self._seats[name]
        except KeyError:
            raise RoomError(f"seat {name.value!r} is not in the room") from None

    def _send(self, origin: SeatName, outgoing: Any) -> None:
        for item in outgoing:
            if isinstance(item, TextReply):
                self._replies.append(item)
                self._events.append(LogEntry(len(self._events) + 1, "text", origin.value, item.mentions, "text", item.text))
                continue
            for recipient in item.to:
                self._seat(recipient)  # fail at send time, naming the seat, not later
            self._events.append(
                LogEntry(len(self._events) + 1, "message", item.sender.value, tuple(s.value for s in item.to), item.type.value, item.summary)
            )
            self._messages.append(item)
            self._queue.extend((recipient, item) for recipient in item.to)

    def _drain(self) -> None:
        while self._queue:
            self._deliveries += 1
            if self._deliveries > self._max_steps:
                raise RoomLoopError(f"more than {self._max_steps} deliveries: the seats are answering each other forever")
            recipient, message = self._queue.popleft()
            wire = f"[{message.sender.value}]: @{recipient.value} {serialize(message)}"
            self._send(recipient, self._seat(recipient).handle(parse_message(wire)))
