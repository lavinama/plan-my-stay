"""Why a message was rejected. Every rejected message gets one `error` reply (BAND fails a silent turn)."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_JSON = "invalid_json"  # not exactly one JSON object
    UNKNOWN_TYPE = "unknown_type"
    UNSUPPORTED_VERSION = "unsupported_version"  # unknown major schema_version
    VALIDATION_FAILED = "validation_failed"  # `fields` lists the failing paths
    UNEXPECTED_SENDER = "unexpected_sender"  # a seat sent a type it doesn't own


class MessageParseError(Exception):
    """Raised by parse_message and build_message. Carries what an `error` reply needs."""

    def __init__(
        self,
        code: ErrorCode,
        detail: str,
        *,
        bad_message_id: str | None = None,
        fields: tuple[str, ...] = (),
        raw_sender: str | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
        self.bad_message_id = bad_message_id
        self.fields = fields
        self.raw_sender = raw_sender  # the unvalidated `from`, so the adapter knows whom to answer
