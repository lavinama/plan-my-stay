"""The shared message contract. Import from here; never read or write the JSON by hand."""

from plan_my_stay.messages.envelope import (
    ALLOWED_SENDERS,
    SCHEMA_VERSION,
    Message,
    build_error_reply,
    build_message,
    parse_message,
    serialize,
)
from plan_my_stay.messages.errors import ErrorCode, MessageParseError
from plan_my_stay.messages.payloads import PAYLOAD_MODELS

__all__ = [
    "ALLOWED_SENDERS",
    "ErrorCode",
    "Message",
    "MessageParseError",
    "PAYLOAD_MODELS",
    "SCHEMA_VERSION",
    "build_error_reply",
    "build_message",
    "parse_message",
    "serialize",
]
