"""Fake seats and a `run` shortcut shared by the process_incoming tests."""

from collections.abc import Sequence

from plan_my_stay.band_adapter.core import Post, RetryTracker, process_incoming
from plan_my_stay.enums import Seat
from plan_my_stay.messages import Message
from plan_my_stay.seat import Outgoing

from .conftest import NAMES

STAY = NAMES[Seat.STAY]
PERSON = "Mario Lavina"


class FakeSeat:
    """A seat that records what it was given and returns what it was told to."""

    def __init__(self, replies: Sequence[Outgoing] | Exception | None = ()) -> None:
        self.replies = replies
        self.handled: list[Message] = []

    def handle(self, message: Message) -> Sequence[Outgoing]:
        self.handled.append(message)
        if isinstance(self.replies, Exception):
            raise self.replies
        return self.replies  # type: ignore[return-value]


class FakeTextSeat(FakeSeat):
    def __init__(self, replies=(), text_replies=()) -> None:
        super().__init__(replies)
        self.text_replies = text_replies
        self.texts: list[tuple[str, str]] = []

    def handle_text(self, sender: str, text: str) -> Sequence[Outgoing]:
        self.texts.append((sender, text))
        if isinstance(self.text_replies, Exception):
            raise self.text_replies
        return self.text_replies  # type: ignore[return-value]


def run(seat, text, sender=STAY, *, own=Seat.ROUTING, directory, retries=None):
    return process_incoming(seat, text, sender, own_seat=own, directory=directory, retries=retries or RetryTracker())


def only_post(result) -> Post:
    assert isinstance(result, tuple) and len(result) == 1, result
    return result[0]
