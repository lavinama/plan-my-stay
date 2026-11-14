"""What every seat looks like to the shared `band_adapter`.

Each person writes only `handle()`; the adapter parses, calls it, posts what it returns and
retries (docs/PLAN.md). A seat is a plain object, so it is tested without BAND.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from plan_my_stay.messages import Message


@dataclass(frozen=True)
class TextReply:
    """Prose for a person in the room (only the Concierge talks to people). Machine messages are `Message`s."""

    text: str
    mentions: tuple[str, ...]  # BAND participant names, e.g. the user who wrote to the Concierge


Outgoing = Message | TextReply


@runtime_checkable
class Seat(Protocol):
    """A BAND seat. `handle` gets one valid message addressed to it and returns what to send.

    Returning an empty sequence means "nothing to say": the adapter then ends the turn with
    `band_no_reply`, because BAND marks a silent turn as failed. `handle` never raises on a bad
    message (the adapter already rejected it with an `error` reply); it may raise on a bug.
    """

    def handle(self, message: Message) -> Sequence[Outgoing]: ...


@runtime_checkable
class TextSeat(Seat, Protocol):
    """A seat that also reads prose from people (the Concierge). The adapter calls this when a body is not JSON."""

    def handle_text(self, sender: str, text: str) -> Sequence[Outgoing]: ...


# `Seat` here is the handler protocol; `plan_my_stay.enums.Seat` is the seat names. Prefer these names in new code.
SeatHandler = Seat
TextSeatHandler = TextSeat
