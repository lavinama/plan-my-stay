"""Reads what the BAND SDK hands the graph, without importing the SDK.

Verified against band-sdk 9.2.1 (`band.runtime.formatters.format_current_inputs`): the last
message of the graph state is one block,

    ## Current input messages (N)
    <a guide paragraph>
    {"message_id": ..., "sender_id": ..., "sender_name": ..., "sender_type": ..., "timestamp": ..., "content": ...}

one JSON record per line, oldest first. The BAND quickstart (written for 9.1.0) says the graph
gets "[Sender Name]: content" instead; that form is still understood as a fallback.
tests/band_adapter/test_sdk.py builds its input with the SDK's own formatter, so a format
change in a newer SDK fails a test instead of silently dropping messages.

`content` already has the platform's `@[[uuid]]` tokens rewritten to `@handle` by the SDK.

UNVERIFIED: the exact live form of that leading mention. The SDK source says the platform puts one
mention token per mention in front of the content ("@handle <body>", or glued to a `{`); the parser
in plan_my_stay.messages and `strip_prose_decoration` handle both, but no live message was seen.
"""

import json
import re
from dataclasses import dataclass

CURRENT_INPUTS_HEADING = "## Current input messages"

_BRACKET_FORM = re.compile(r"^\[(?P<sender>[^\]]+)\]:\s*(?P<text>.*)$", re.DOTALL)
_BAND_PREFIX = re.compile(r"^\[[^\]]*\]:\s*")
_LEADING_HANDLES = re.compile(r"^\s*(?:@\S+(?:\s+|$))+")  # the SDK's own strip_leading_mentions rule


@dataclass(frozen=True)
class IncomingInput:
    """One message delivered to the seat this turn."""

    sender_name: str  # BAND display name; the sender id when BAND has no name for them
    content: str  # exactly what the sender posted, platform mention tokens included
    message_id: str | None = None
    sender_id: str | None = None
    sender_type: str | None = None  # "User" or "Agent" in the SDK's records


def _record(line: str) -> IncomingInput | None:
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("content"), str):
        return None
    sender_id = data.get("sender_id")
    name = data.get("sender_name") or sender_id
    if not isinstance(name, str) or not name.strip():
        return None
    return IncomingInput(
        sender_name=name,
        content=data["content"],
        message_id=data.get("message_id") if isinstance(data.get("message_id"), str) else None,
        sender_id=sender_id if isinstance(sender_id, str) else None,
        sender_type=data.get("sender_type") if isinstance(data.get("sender_type"), str) else None,
    )


def extract_inputs(text: str) -> tuple[IncomingInput, ...]:
    """The messages of one turn, oldest first; an empty tuple when the text is in no known format."""
    if CURRENT_INPUTS_HEADING in text:
        records = (_record(line.strip()) for line in text.split(CURRENT_INPUTS_HEADING, 1)[1].splitlines())
        return tuple(r for r in records if r is not None)
    match = _BRACKET_FORM.match(text.strip())
    if match:
        return (IncomingInput(sender_name=match.group("sender"), content=match.group("text")),)
    return ()


def strip_prose_decoration(text: str) -> str:
    """Drop a leading "[Sender]: " prefix and the leading @handles that addressed the seat, keeping the rest verbatim."""
    return _LEADING_HANDLES.sub("", _BAND_PREFIX.sub("", text.strip(), count=1), count=1).strip()
