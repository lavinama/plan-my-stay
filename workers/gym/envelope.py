"""Local stand-in for the shared BAND message envelope module.

docs/PLAN.md's "How agents share information" section says every seat sends
with one shared `build_message(...)` / `parse_message(text)` pair, built once
by Mario, so nobody hand-writes or hand-parses the envelope JSON. That shared
module doesn't exist yet, so this file reproduces its contract exactly
(field names, validation rules, error shape) for the Gym agent to depend on
now. When the real shared module lands, swap the import at the top of
handle.py from `from workers.gym import envelope` to the shared one; every
function name and signature here is meant to match it so that swap is a
one-line change, not a rewrite.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "1.0"

# The full set of message types from docs/PLAN.md's message types table.
KNOWN_TYPES = {
    "request",
    "proposal",
    "zone_fit",
    "stay_shortlist",
    "stay_pick",
    "workout_slot",
    "preference_learned",
    "conflict",
    "verdict",
    "error",
    "route_request",
    "route_reply",
}

_REQUIRED_ENVELOPE_KEYS = (
    "schema_version",
    "type",
    "message_id",
    "trip_id",
    "user_id",
    "state_version",
    "from",
    "to",
    "sent_at",
    "summary",
    "payload",
)

# Matches a leading "[Sender]: " prefix, then leading "@handle" mentions,
# both of which some BAND adapters prepend to the raw JSON body.
_SENDER_PREFIX_RE = re.compile(r"^\[[^\]]+\]:\s*")
_MENTION_RE = re.compile(r"^@\S+\s*")


def build_message(
    *,
    type: str,
    from_: str,
    to: list[str],
    trip_id: str,
    user_id: str,
    state_version: int,
    summary: str,
    payload: dict[str, Any],
    reply_to: str | None = None,
) -> dict[str, Any]:
    """Assemble one envelope dict. Code builds the envelope, never an LLM."""
    return {
        "schema_version": SCHEMA_VERSION,
        "type": type,
        "message_id": str(uuid.uuid4()),
        "reply_to": reply_to,
        "trip_id": trip_id,
        "user_id": user_id,
        "state_version": state_version,
        "from": from_,
        "to": list(to),
        "sent_at": datetime.now().astimezone().isoformat(),
        "summary": summary,
        "payload": payload,
    }


def parse_message(text: str) -> dict[str, Any]:
    """Strip BAND's own text wrapping, then parse the JSON body.

    Some BAND adapters prepend a "[Sender]: " label and/or leading @handle
    mentions before the one JSON object a machine message must be. Strip
    those, then json.loads the rest. Anything else (prose, code fences,
    trailing text) is a malformed message and json.loads will raise, which
    callers should treat as a parse failure, not a silent pass.
    """
    stripped = _SENDER_PREFIX_RE.sub("", text, count=1)
    stripped = _MENTION_RE.sub("", stripped, count=1)
    return json.loads(stripped)


def validate_incoming(message: dict[str, Any]) -> list[str]:
    """Check the envelope shape only, never payload contents.

    Returns a list of problem strings; empty means valid. This is
    deliberately separate from type-membership checking (is `type` one of
    KNOWN_TYPES) and from any business-logic validation of what a given
    type's payload should contain. Those live in handle.py, because a seat
    that doesn't own a type still needs to tell a well-formed envelope from
    a broken one before deciding whether to answer.
    """
    problems: list[str] = []

    if not isinstance(message, dict):
        return ["message is not a JSON object"]

    for key in _REQUIRED_ENVELOPE_KEYS:
        if key not in message:
            problems.append(f"missing required field: {key}")

    if "schema_version" in message:
        version = message["schema_version"]
        if not isinstance(version, str) or not (
            version == "1.0" or version.startswith("1.")
        ):
            problems.append(
                f"unsupported schema_version major: {version!r} (expected major '1')"
            )

    if "type" in message:
        if not isinstance(message["type"], str) or not message["type"]:
            problems.append("type must be a non-empty string")

    if "to" in message and not isinstance(message["to"], list):
        problems.append("to must be a list")

    if "payload" in message and not isinstance(message["payload"], dict):
        problems.append("payload must be an object")

    # reply_to is optional/nullable: no check needed beyond its absence
    # being fine, which the required-keys loop above already allows.

    return problems


def error_for(message: dict[str, Any], code: str, fields: list[str]) -> dict[str, Any]:
    """Build an `error` reply to a message this seat could not handle."""
    sender = message.get("from", "unknown")
    return build_message(
        type="error",
        from_="gym",
        to=[sender],
        trip_id=message.get("trip_id", ""),
        user_id=message.get("user_id", ""),
        state_version=message.get("state_version", 0),
        reply_to=message.get("message_id"),
        summary=f"Gym agent could not process message: {code}",
        payload={
            "code": code,
            "bad_message_id": message.get("message_id"),
            "fields": fields,
        },
    )
