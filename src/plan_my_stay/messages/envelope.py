"""The envelope every message shares, and the only code that builds or parses it.

Nobody writes or reads the JSON by hand: seats send with `build_message` + `serialize`
and receive with `parse_message`. Code builds the envelope (ids, timestamps, versions);
an LLM only ever fills payload fields.
"""

import json
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    SerializeAsAny,
    StringConstraints,
    ValidationError,
    model_validator,
)

from plan_my_stay.enums import SPECIALISTS, MessageType, Seat
from plan_my_stay.messages.errors import ErrorCode, MessageParseError
from plan_my_stay.messages.payloads import PAYLOAD_MODELS, ErrorPayload
from plan_my_stay.models import Frozen
from plan_my_stay.timeutil import now

SCHEMA_VERSION = "1.0"
SUPPORTED_MAJOR = 1

_UUID_PATTERN = r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"
UuidText = Annotated[str, StringConstraints(pattern=_UUID_PATTERN)]

# Which seats may send which type. Anything else is rejected as `unexpected_sender`.
ALLOWED_SENDERS: dict[MessageType, frozenset[Seat]] = {
    MessageType.REQUEST: frozenset({Seat.CONCIERGE}),
    # The Concierge sends the merged plan to the Verifier as a proposal (see ProposalPayload).
    MessageType.PROPOSAL: SPECIALISTS | {Seat.CONCIERGE},
    MessageType.ZONE_FIT: frozenset({Seat.RESTAURANT, Seat.GYM}),
    MessageType.STAY_SHORTLIST: frozenset({Seat.STAY}),
    MessageType.STAY_PICK: frozenset({Seat.STAY}),
    MessageType.WORKOUT_SLOT: frozenset({Seat.GYM}),
    MessageType.PREFERENCE_LEARNED: SPECIALISTS | {Seat.CONCIERGE},
    MessageType.CONFLICT: frozenset({Seat.VERIFIER}),
    MessageType.VERDICT: frozenset({Seat.VERIFIER}),
    MessageType.ERROR: frozenset(Seat),
    MessageType.ROUTE_REQUEST: frozenset(Seat) - {Seat.ROUTING},
    MessageType.ROUTE_REPLY: frozenset({Seat.ROUTING}),
}


class MessageHead(Frozen):
    """Every envelope field except `payload`."""

    schema_version: str = Field(pattern=r"^\d+\.\d+$")
    type: MessageType
    message_id: UuidText
    reply_to: UuidText | None = None
    trip_id: str = Field(min_length=1)  # the BAND room id or a UUID; also the Cognee session id
    user_id: str = Field(min_length=1)
    state_version: int = Field(ge=0)
    sender: Seat = Field(alias="from")
    to: tuple[Seat, ...] = Field(min_length=1)  # every send names at least one recipient
    sent_at: AwareDatetime
    summary: str = Field(min_length=1, max_length=300)  # one readable line: people watch the room in Band Desktop


class Message(MessageHead):
    payload: SerializeAsAny[BaseModel]

    @model_validator(mode="after")
    def _payload_matches_type(self) -> "Message":
        expected = PAYLOAD_MODELS[self.type]
        if not isinstance(self.payload, expected):
            raise ValueError(f"payload must be a {expected.__name__} for type {self.type.value}")
        return self


def serialize(message: Message) -> str:
    """The message body: exactly one JSON object, nothing else. Unknown values stay `null`, never omitted."""
    return message.model_dump_json(by_alias=True)


_BAND_PREFIX = re.compile(r"^\[[^\]]*\]:\s*")  # "[Sender Name]: " added by some adapters
_LEADING_MENTIONS = re.compile(r"^(?:@[^\s{]+\s*)+")  # "@handle " in front of the JSON


def _strip_band_decoration(text: str) -> str:
    text = _BAND_PREFIX.sub("", text.strip(), count=1)
    return _LEADING_MENTIONS.sub("", text, count=1).strip()


def _field_paths(error: ValidationError, prefix: str = "") -> list[str]:
    return [prefix + ".".join(str(part) for part in e["loc"]) for e in error.errors()]


def _validate(raw: Mapping[str, Any]) -> Message:
    """Validate a raw envelope dict. Raises MessageParseError; never returns a half-valid message."""
    raw_id = raw.get("message_id")
    bad_id = raw_id if isinstance(raw_id, str) else None
    raw_from = raw.get("from")
    raw_sender = str(raw_from) if raw_from is not None else None

    def fail(code: ErrorCode, detail: str, fields: Iterable[str] = ()) -> MessageParseError:
        return MessageParseError(code, detail, bad_message_id=bad_id, fields=tuple(fields), raw_sender=raw_sender)

    version = str(raw.get("schema_version", ""))
    version_match = re.fullmatch(r"(\d+)\.(\d+)", version)
    if version_match is None:
        raise fail(ErrorCode.VALIDATION_FAILED, "schema_version must look like '1.0'", ["schema_version"])
    if int(version_match.group(1)) != SUPPORTED_MAJOR:
        raise fail(ErrorCode.UNSUPPORTED_VERSION, f"unsupported major schema_version {version}")

    if "type" not in raw:
        raise fail(ErrorCode.VALIDATION_FAILED, "type is missing", ["type"])
    try:
        msg_type = MessageType(raw["type"])
    except ValueError:
        raise fail(ErrorCode.UNKNOWN_TYPE, f"unknown message type {raw['type']!r}") from None

    fields: list[str] = []
    try:
        MessageHead.model_validate(raw)
    except ValidationError as exc:
        fields += _field_paths(exc)

    payload: BaseModel | None = None
    payload_model = PAYLOAD_MODELS[msg_type]
    raw_payload = raw.get("payload")
    try:
        payload = raw_payload if isinstance(raw_payload, payload_model) else payload_model.model_validate(raw_payload)
    except ValidationError as exc:
        fields += _field_paths(exc, prefix="payload.")

    if fields or payload is None:
        raise fail(ErrorCode.VALIDATION_FAILED, f"invalid: {', '.join(fields)}", fields)

    message = Message.model_validate({**raw, "payload": payload})
    _check_envelope_rules(message, bad_id, raw_sender)
    return message


def _check_envelope_rules(message: Message, bad_id: str | None, raw_sender: str | None) -> None:
    if message.sender not in ALLOWED_SENDERS[message.type]:
        raise MessageParseError(
            ErrorCode.UNEXPECTED_SENDER,
            f"{message.sender.value} may not send {message.type.value}",
            bad_message_id=bad_id,
            fields=("from",),
            raw_sender=raw_sender,
        )
    if message.sender in message.to or len(set(message.to)) != len(message.to):
        raise MessageParseError(
            ErrorCode.VALIDATION_FAILED,
            "`to` must list each recipient once and not include the sender",
            bad_message_id=bad_id,
            fields=("to",),
            raw_sender=raw_sender,
        )


def parse_message(text: str) -> Message:
    """Strip what BAND adds (a "[Sender]: " prefix, leading @handles), then parse and validate one JSON object."""
    body = _strip_band_decoration(text)
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MessageParseError(ErrorCode.INVALID_JSON, f"body is not one JSON object: {exc.msg}") from None
    if not isinstance(raw, dict):
        raise MessageParseError(ErrorCode.INVALID_JSON, "body must be a JSON object")
    return _validate(raw)


def build_message(
    *,
    type: MessageType | str,  # noqa: A002 - the wire field is called `type`
    sender: Seat,
    to: Iterable[Seat],
    trip_id: str,
    user_id: str,
    state_version: int,
    summary: str,
    payload: BaseModel | Mapping[str, Any],
    reply_to: str | None = None,
    message_id: str | None = None,
    sent_at: datetime | None = None,
) -> Message:
    """Build and validate a message. Code fills the envelope; the caller supplies only the payload."""
    return _validate(
        {
            "schema_version": SCHEMA_VERSION,
            "type": type,
            "message_id": message_id or str(uuid.uuid4()),
            "reply_to": reply_to,
            "trip_id": trip_id,
            "user_id": user_id,
            "state_version": state_version,
            "from": sender,
            "to": tuple(to),
            "sent_at": sent_at or now(),
            "summary": summary,
            "payload": payload,
        }
    )


def build_error_reply(
    error: MessageParseError,
    *,
    sender: Seat,
    to: Seat,
    trip_id: str,
    user_id: str,
    state_version: int,
) -> Message:
    """The one answer a rejected message gets: the code, the bad message's id and the failing fields.

    A sender whose `message_id` is not a UUID still gets its answer: `reply_to` is left null (it must be a UUID)
    and the payload still carries the id it sent, so it can match the error to its message."""
    bad_id = error.bad_message_id
    reply_to = bad_id if bad_id is not None and re.fullmatch(_UUID_PATTERN, bad_id) else None
    return build_message(
        type=MessageType.ERROR,
        sender=sender,
        to=[to],
        trip_id=trip_id,
        user_id=user_id,
        state_version=state_version,
        summary=f"Rejected a message: {error.code.value}",
        reply_to=reply_to,
        payload=ErrorPayload(code=error.code.value, bad_message_id=bad_id, fields=error.fields),
    )
