"""The adapter's logic without the BAND SDK: one incoming text in, the posts to send out.

`process_incoming` is everything docs/SEAT_PROTOCOL.md section 6 asks of the adapter. It never
raises for a bad message or a failing seat, and it never returns nothing without saying so:
the result is either at least one `Post` or a `NoReply`, because BAND marks a silent turn as
failed (docs/PLAN.md, "Every message gets one answer"). `sdk.py` turns that result into
`band_send_message` / `band_no_reply` calls.
"""

import hashlib
import json
import logging
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field

from plan_my_stay.band_adapter.directory import SeatDirectory
from plan_my_stay.band_adapter.inputs import strip_prose_decoration
from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import (
    ErrorCode,
    Message,
    MessageParseError,
    build_error_reply,
    build_message,
    parse_message,
    serialize,
)
from plan_my_stay.messages.payloads import ErrorPayload
from plan_my_stay.seat import Outgoing, TextReply, TextSeat
from plan_my_stay.seat import Seat as SeatHandler

logger = logging.getLogger(__name__)

INTERNAL_ERROR_CODE = "internal_error"  # not in ErrorCode: that enum only holds codes parse_message raises
DEFAULT_MAX_RETRIES = 2
_VISIBLE_CATEGORIES = frozenset("LNPS")  # letters, numbers, punctuation, symbols: BAND's own visible-content rule
_APOLOGY = "Sorry, something went wrong while handling your message. Please try again in a moment."


def _has_visible_content(text: str) -> bool:
    return any(unicodedata.category(char)[0] in _VISIBLE_CATEGORIES for char in text)


@dataclass(frozen=True)
class Post:
    """One `band_send_message` call: a body and the display names to mention. Never empty, never without a mention."""

    content: str
    mentions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _has_visible_content(self.content):
            raise ValueError("a post needs visible content: BAND refuses a blank message")
        if isinstance(self.mentions, str):
            raise ValueError("mentions must be a tuple of display names, not one string")
        mentions = tuple(self.mentions)
        if not mentions or any(not m.strip() for m in mentions):
            raise ValueError("a post needs at least one mention: band_send_message refuses a send without one")
        object.__setattr__(self, "mentions", mentions)


@dataclass(frozen=True)
class NoReply:
    """End the turn with `band_no_reply`. `reason` is for the log; all NoReply values are equal."""

    reason: str = field(default="", compare=False)


Result = tuple[Post, ...] | NoReply


class RetryTracker:
    """Counts how often a sender re-sends the same rejected message, so a retry loop ends.

    The first rejection and `max_retries` re-sends are answered with an `error`; after that the
    adapter stays silent. Thread-safe, and bounded: the oldest entries are forgotten first.
    """

    def __init__(self, max_retries: int = DEFAULT_MAX_RETRIES, *, capacity: int = 1024) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.max_retries = max_retries
        self._capacity = capacity
        self._counts: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._lock = threading.Lock()

    def allow_reply(self, sender: str, bad_message_key: str) -> bool:
        """Record one more sighting of this bad message; True while an `error` reply is still allowed."""
        key = (sender.casefold(), bad_message_key)
        with self._lock:
            count = self._counts.pop(key, 0) + 1
            self._counts[key] = count
            while len(self._counts) > self._capacity:
                self._counts.popitem(last=False)
        return count <= self.max_retries + 1


def process_incoming(
    seat: SeatHandler,
    incoming_text: str,
    sender_name: str,
    *,
    own_seat: Seat,
    directory: SeatDirectory,
    retries: RetryTracker,
) -> Result:
    """Turn one incoming BAND message body into the posts to send, or a `NoReply`."""
    sender = (sender_name or "").strip()
    try:
        message = parse_message(incoming_text)
    except MessageParseError as error:
        result = _on_rejected(seat, incoming_text, sender, error, own_seat, directory, retries)
    else:
        result = _on_message(seat, message, sender, own_seat, directory)
    _log_sizes(own_seat, result)
    return result


# --- valid messages ---------------------------------------------------------------------------


def _on_message(seat: SeatHandler, message: Message, sender: str, own_seat: Seat, directory: SeatDirectory) -> Result:
    if own_seat not in message.to:
        logger.warning(
            "seat %s: message %s (%s from %s) is not addressed to this seat (to=%s); handling it anyway",
            own_seat.value, message.message_id, message.type.value, message.sender.value, [s.value for s in message.to],
        )
    try:
        posts = _posts_for(seat.handle(message), own_seat, directory, sender or directory.name_of(message.sender))
    except Exception:
        logger.exception(
            "seat %s: handle() failed on %s message %s (trip %s, from %s)",
            own_seat.value, message.type.value, message.message_id, message.trip_id, message.sender.value,
        )
        return _internal_error(message, sender, own_seat, directory)
    return posts or NoReply("the seat had nothing to say")


def _internal_error(message: Message, sender: str, own_seat: Seat, directory: SeatDirectory) -> Result:
    """Answer a message whose handler crashed with an `error` (code internal_error); the exception text stays in the log."""
    target = _reply_target(message.sender.value, sender, own_seat, directory)
    if target is None:
        logger.error("seat %s: no one to tell about the failed message %s", own_seat.value, message.message_id)
        return NoReply("handler failed and the sender is unknown")
    to_seat, mention = target
    reply = build_message(
        type=MessageType.ERROR,
        sender=own_seat,
        to=[to_seat],
        trip_id=message.trip_id,
        user_id=message.user_id,
        state_version=message.state_version,
        summary=f"{own_seat.value} hit an internal error handling {message.type.value}",
        reply_to=message.message_id,
        payload=ErrorPayload(code=INTERNAL_ERROR_CODE, bad_message_id=message.message_id),
    )
    return (Post(serialize(reply), (mention,)),)


def _posts_for(outgoing: Iterable[Outgoing], own_seat: Seat, directory: SeatDirectory, fallback_mention: str) -> tuple[Post, ...]:
    """Convert what a seat returned. Raises TypeError for anything that is not a Message or TextReply."""
    posts: list[Post] = []
    for item in tuple(outgoing):
        if isinstance(item, Message):
            posts.append(_message_post(item, own_seat, directory))
        elif isinstance(item, TextReply):
            post = _text_post(item, own_seat, fallback_mention)
            if post is not None:
                posts.append(post)
        else:
            raise TypeError(f"a seat must return Message or TextReply objects, got {type(item).__name__}")
    return tuple(posts)


def _message_post(message: Message, own_seat: Seat, directory: SeatDirectory) -> Post:
    if message.sender is not own_seat:
        logger.warning(
            "seat %s emitted a %s message with from=%s; receivers will see that sender",
            own_seat.value, message.type.value, message.sender.value,
        )
    return Post(serialize(message), tuple(directory.name_of(seat) for seat in message.to))


def _text_post(reply: TextReply, own_seat: Seat, fallback_mention: str) -> Post | None:
    if not _has_visible_content(reply.text):
        logger.warning("seat %s: dropping a blank text reply", own_seat.value)
        return None
    mentions = tuple(m for m in reply.mentions if m and m.strip()) or ((fallback_mention,) if fallback_mention.strip() else ())
    if not mentions:
        logger.error("seat %s: dropping a text reply with nobody to mention", own_seat.value)
        return None
    return Post(reply.text, mentions)


# --- prose and rejected messages ----------------------------------------------------------------


def _on_rejected(
    seat: SeatHandler, text: str, sender: str, error: MessageParseError, own_seat: Seat, directory: SeatDirectory, retries: RetryTracker
) -> Result:
    # Prose from a person goes to a seat that reads prose. A seat that wrote it is a bug, so it gets an error instead.
    if error.code is ErrorCode.INVALID_JSON and isinstance(seat, TextSeat) and directory.seat_of(sender) is None:
        return _on_text(seat, strip_prose_decoration(text), sender, own_seat, directory)

    key = error.bad_message_id or "body:" + hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    who = sender or error.raw_sender or "unknown"
    if not retries.allow_reply(who, key):
        logger.warning(
            "seat %s: staying silent about message %s from %s: already rejected %d times (retry limit %d)",
            own_seat.value, key, who, retries.max_retries + 1, retries.max_retries,
        )
        return NoReply("retry limit reached")
    try:
        return _rejection(text, sender, error, own_seat, directory)
    except Exception:
        logger.exception("seat %s: could not build an error reply for a rejected message from %s", own_seat.value, who)
        return NoReply("could not build the error reply")


def _rejection(text: str, sender: str, error: MessageParseError, own_seat: Seat, directory: SeatDirectory) -> Result:
    target = _reply_target(error.raw_sender, sender, own_seat, directory)
    if target is None:
        logger.error("seat %s: rejected a message from an unknown sender and cannot answer it", own_seat.value)
        return NoReply("no one to answer")
    to_seat, mention = target
    trip_id, user_id, state_version = _envelope_hints(text)
    # A bad message whose own id is not a UUID still gets this answer: build_error_reply leaves reply_to null.
    reply = build_error_reply(error, sender=own_seat, to=to_seat, trip_id=trip_id, user_id=user_id, state_version=state_version)
    return (Post(serialize(reply), (mention,)),)


def _on_text(seat: TextSeat, text: str, sender: str, own_seat: Seat, directory: SeatDirectory) -> Result:
    try:
        posts = _posts_for(seat.handle_text(sender, text), own_seat, directory, sender)
    except Exception:
        logger.exception("seat %s: handle_text() failed for a message from %s", own_seat.value, sender)
        return (Post(_APOLOGY, (sender,)),) if sender else NoReply("handle_text failed and the sender is unknown")
    return posts or NoReply("the seat had nothing to say")


def _reply_target(raw_sender: str | None, sender_name: str, own_seat: Seat, directory: SeatDirectory) -> tuple[Seat, str] | None:
    """Whom an error goes to: (the `to` seat, the display name to mention), or None when nobody can be addressed.

    The seat named in the message's `from`, else the seat behind the BAND sender, else the BAND
    sender by display name (a person). An error's `to` must name a seat that is not the sender,
    so for a person it is only nominal (the first other seat); the mention is what routes it.
    """
    claimed = _as_seat(raw_sender)
    for candidate in (claimed, directory.seat_of(sender_name)):
        if candidate is not None and candidate is not own_seat:
            return candidate, directory.name_of(candidate)
    if not sender_name:
        return None
    return next(s for s in Seat if s is not own_seat), sender_name


def _as_seat(raw: str | None) -> Seat | None:
    try:
        return Seat(raw) if raw is not None else None
    except ValueError:
        return None


def _envelope_hints(text: str) -> tuple[str, str, int]:
    """trip_id, user_id and state_version of a rejected message when it is a JSON object that has them."""
    start, end = text.find("{"), text.rfind("}")
    raw: object = None
    if 0 <= start < end:
        try:
            raw = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raw = None
    data = raw if isinstance(raw, dict) else {}
    trip, user, version = data.get("trip_id"), data.get("user_id"), data.get("state_version")
    return (
        trip if isinstance(trip, str) and trip.strip() else "unknown",
        user if isinstance(user, str) and user.strip() else "unknown",
        version if isinstance(version, int) and not isinstance(version, bool) and version >= 0 else 0,
    )


def _log_sizes(own_seat: Seat, result: Result) -> None:
    """BAND documents no message length limit, so every outgoing body's size goes in the log to learn it.

    UNVERIFIED: the platform's limit on a message's length. band-sdk 9.2.1 imposes none on messages
    (band/platform/posting.py only cuts *events* at 16,384 characters, and says messages have no such
    cap), but the platform or the network path may. scripts/band_roundtrip.py --probe-sizes measures it.
    """
    if isinstance(result, NoReply):
        logger.info("seat %s: no reply (%s)", own_seat.value, result.reason or "no reason given")
        return
    for post in result:
        logger.info(
            "seat %s: outgoing body %d chars, %d bytes, mentions=%s",
            own_seat.value, len(post.content), len(post.content.encode("utf-8")), list(post.mentions),
        )
