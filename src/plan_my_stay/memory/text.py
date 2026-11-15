"""How memory entries are written as plain conversational text, and read back from recall output.

Cognee ignores most structured fields (docs/PLAN.md, "Only some fields reach the graph"), so every entry is
one line of text that starts with `agent=<name>`:

    agent=concierge kind=preference user=u1 trip=t1 key=diet at=<iso> value=["vegan"] evidence=said so
    agent=concierge kind=trip_state trip=t1 user=u1 state_version=3 at=<iso> state={...compact JSON...}
    agent=gym kind=city_fact city="New York, NY" source=https://... fetched_at=<iso> fact=Open until 22:00
    agent=gym kind=trace trip=t1 user=u1 step=search at=<iso> outcome=2 of 5 pages usable

A field is `name=token`. A token with spaces or markup is written as a JSON string. `value` and `state` are
JSON. The last field (`evidence`, `fact` or `outcome`) is free text to the end of the line, with newlines
folded to spaces, so free text can never start a second entry or override a field. Parsing takes the first
`agent=` on a line (recall may put a prefix in front), stops at the first thing it does not understand, and
ignores lines that are not entries, because recall is text retrieval and returns whatever it matched.
"""

import datetime as dt
import json
import logging
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import JsonValue, ValidationError

from plan_my_stay.state import TripState

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000  # a free-text field (evidence, fact, outcome) is cut to this
MAX_VALUE_CHARS = 4000  # a preference value, as JSON, may not be longer
PREFERENCE_KEY = re.compile(r"^(?!model_)[a-z][a-z0-9_]{0,63}$")

_AGENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_PLAIN_TOKEN = re.compile(r'^[^\s"\[\]{}]+$')
_LINE_BREAKS = re.compile(r"[\r\n\v\f\x1c-\x1e\x85  ]+")
_AGENT_START = re.compile(r"(?:^|\s)agent=")
_FIELD_NAME = re.compile(r"[a-z][a-z_]*=")
_TOKEN = re.compile(r"\S+")
_TAIL_FIELDS = frozenset({"evidence", "fact", "outcome"})
_JSON_FIELDS = frozenset({"value", "state"})
_DECODER = json.JSONDecoder()
_NO_VALUE = object()


# --- records read back ---------------------------------------------------------------------


@dataclass(frozen=True)
class PreferenceRecord:
    user_id: str
    trip_id: str
    key: str
    value: JsonValue
    evidence: str
    agent: str
    at: dt.datetime | None
    seq: int  # position in the text it came from: a tie-breaker when two entries have the same time


@dataclass(frozen=True)
class TripStateRecord:
    trip_id: str
    user_id: str
    state_version: int
    state: TripState
    at: dt.datetime | None
    seq: int


@dataclass(frozen=True)
class CityFactRecord:
    city: str
    fact: str
    source_url: str
    fetched_at: dt.datetime
    agent: str


# --- writing -------------------------------------------------------------------------------


def preference_line(
    *, agent: str, user_id: str, trip_id: str, key: str, value: JsonValue, evidence: str, at: dt.datetime
) -> str:
    """One learned preference. Raises ValueError for a bad key, a blank id, a value that is not JSON or is too big."""
    if not PREFERENCE_KEY.match(key):
        raise ValueError("preference key must be lower snake case, start with a letter, and be at most 64 characters")
    return _line(
        agent,
        "preference",
        [("user", require_id(user_id, "user_id")), ("trip", require_id(trip_id, "trip_id")), ("key", key), ("at", _time(at, "at"))],
        [("value", _dump_json(value))],
        ("evidence", evidence),
    )


def trip_state_line(*, agent: str, state: TripState, at: dt.datetime) -> str:
    """A copy of the trip state. The labels (`trip`, `user`, `state_version`) mirror what is inside the JSON."""
    return _line(
        agent,
        "trip_state",
        [
            ("trip", require_id(state.trip_id, "trip_id")),
            ("user", require_id(state.user_id, "user_id")),
            ("state_version", str(state.state_version)),
            ("at", _time(at, "at")),
        ],
        [("state", state.model_dump_json())],
    )


def city_fact_line(*, agent: str, city: str, fact: str, source_url: str, fetched_at: dt.datetime) -> str:
    """A shared place fact with its source and fetch time."""
    if re.search(r"\s", source_url) or not source_url:
        raise ValueError("source_url must be a URL without spaces")
    if not fact.strip():
        raise ValueError("fact must not be empty")
    return _line(
        agent,
        "city_fact",
        [("city", require_id(city, "city")), ("source", source_url), ("fetched_at", _time(fetched_at, "fetched_at"))],
        [],
        ("fact", fact),
    )


def trace_line(*, agent: str, trip_id: str, user_id: str, step: str, outcome: str, at: dt.datetime) -> str:
    """One agent step and what came of it."""
    fields = [("trip", require_id(trip_id, "trip_id")), ("user", require_id(user_id, "user_id")), ("step", require_id(step, "step")), ("at", _time(at, "at"))]
    return _line(agent, "trace", fields, [], ("outcome", outcome))


def normalize_agent(agent: str) -> str:
    """The agent name as written (`str(Seat.GYM)` is "gym"). Raises ValueError if it is not a single word."""
    name = str(agent)
    if not _AGENT.match(name):
        raise ValueError("agent must be one word of letters, digits, underscore, dot or dash")
    return name


def _line(
    agent: str,
    kind: str,
    tokens: list[tuple[str, str]],
    json_fields: list[tuple[str, str]],
    tail: tuple[str, str] | None = None,
) -> str:
    parts = [f"agent={normalize_agent(agent)}", f"kind={kind}"]
    parts += [f"{name}={_token(value)}" for name, value in tokens]
    parts += [f"{name}={value}" for name, value in json_fields]
    if tail is not None:
        parts.append(f"{tail[0]}={_free_text(tail[1])}")
    return " ".join(parts)


def _token(value: str) -> str:
    return value if _PLAIN_TOKEN.match(value) else json.dumps(value, ensure_ascii=False)


def _free_text(value: str) -> str:
    return _LINE_BREAKS.sub(" ", value)[:MAX_TEXT_CHARS].strip()


def require_id(value: str, name: str) -> str:
    """`value` unchanged, or ValueError if it is not a non-blank string. For user, trip and city ids."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _time(value: dt.datetime, name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.isoformat()


def _dump_json(value: JsonValue) -> str:
    try:
        dumped = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("preference value must be plain JSON (no NaN, sets or objects)") from exc
    if len(dumped) > MAX_VALUE_CHARS:
        raise ValueError(f"preference value is longer than {MAX_VALUE_CHARS} characters as JSON")
    return dumped


# --- reading -------------------------------------------------------------------------------


def parse_line(line: str) -> Mapping[str, Any] | None:
    """The fields of the first entry on `line`, or None if there is none. Stops at the first thing it cannot read."""
    start = _AGENT_START.search(line)
    if start is None:
        return None
    pos = start.end() - len("agent=")
    fields: dict[str, Any] = {}
    while pos < len(line):
        while pos < len(line) and line[pos].isspace():
            pos += 1
        found = _FIELD_NAME.match(line, pos)
        if found is None:
            break
        name, pos = found.group()[:-1], found.end()
        if name in fields:
            break  # a repeated name is never ours
        if name in _TAIL_FIELDS:
            fields[name] = line[pos:].strip()
            break
        try:
            fields[name], pos = _read_value(line, pos, name)
        except ValueError:
            break
    agent = fields.get("agent")
    return MappingProxyType(fields) if isinstance(agent, str) and agent else None


def _read_value(line: str, pos: int, name: str) -> tuple[Any, int]:
    if pos >= len(line) or line[pos].isspace():
        raise ValueError("empty value")
    first = line[pos]
    if name in _JSON_FIELDS or first == '"':
        try:
            value, end = _DECODER.raw_decode(line, pos)
        except ValueError:
            if first in '"[{':
                raise
        else:
            if name in _JSON_FIELDS or isinstance(value, str):
                return value, end
            raise ValueError("a token is a string")
    token = _TOKEN.match(line, pos)
    if token is None:
        raise ValueError("empty value")
    return token.group(), token.end()


def _lines(texts: Iterable[str]) -> Iterator[tuple[int, str]]:
    seq = 0
    for chunk in texts:
        for line in re.split(r"[\r\n]+", chunk):
            if line.strip():
                yield seq, line
                seq += 1


def _entries(texts: Iterable[str], kind: str) -> Iterator[tuple[int, Mapping[str, Any]]]:
    for seq, line in _lines(texts):
        fields = parse_line(line)
        if fields is not None and _kind_of(fields) == kind:
            yield seq, fields


def _kind_of(fields: Mapping[str, Any]) -> str | None:
    kind = fields.get("kind")
    if isinstance(kind, str):
        return kind
    if "state" in fields:
        return "trip_state"
    if "key" in fields and "value" in fields:
        return "preference"
    if "city" in fields and "fact" in fields:
        return "city_fact"
    return None


def _text_field(fields: Mapping[str, Any], name: str) -> str | None:
    value = fields.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _read_time(fields: Mapping[str, Any], name: str) -> dt.datetime | None:
    raw = fields.get(name)
    if not isinstance(raw, str):
        return None
    try:
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def parse_preferences(texts: Iterable[str]) -> tuple[PreferenceRecord, ...]:
    """Every preference entry in `texts`, in order. Entries missing a user, a valid key or a value are ignored."""
    records = []
    for seq, fields in _entries(texts, "preference"):
        user, key = _text_field(fields, "user"), fields.get("key")
        value = fields.get("value", _NO_VALUE)
        if user is None or not isinstance(key, str) or not PREFERENCE_KEY.match(key) or value is _NO_VALUE:
            continue
        records.append(
            PreferenceRecord(
                user_id=user,
                trip_id=_text_field(fields, "trip") or "",
                key=key,
                value=value,
                evidence=str(fields.get("evidence", "")),
                agent=str(fields["agent"]),
                at=_read_time(fields, "at"),
                seq=seq,
            )
        )
    return tuple(records)


def parse_trip_states(texts: Iterable[str]) -> tuple[TripStateRecord, ...]:
    """Every readable trip-state copy in `texts`. Unreadable ones are skipped with a warning (no content is logged)."""
    records = []
    for seq, fields in _entries(texts, "trip_state"):
        record = _trip_state_record(seq, fields)
        if record is None:
            logger.warning("Ignoring an unreadable trip state copy found in memory")
            continue
        records.append(record)
    return tuple(records)


def _trip_state_record(seq: int, fields: Mapping[str, Any]) -> TripStateRecord | None:
    trip, user, raw_state = _text_field(fields, "trip"), _text_field(fields, "user"), fields.get("state")
    version = fields.get("state_version")
    if trip is None or user is None or not isinstance(raw_state, dict) or not isinstance(version, str) or not version.isdigit():
        return None
    try:
        state = TripState.model_validate(raw_state)
    except ValidationError:
        return None
    return TripStateRecord(trip_id=trip, user_id=user, state_version=int(version), state=state, at=_read_time(fields, "at"), seq=seq)


def parse_city_facts(texts: Iterable[str]) -> tuple[CityFactRecord, ...]:
    """Every city-fact entry in `texts` that has a city, a fact, a source and a timezone-aware fetch time."""
    records = []
    for _, fields in _entries(texts, "city_fact"):
        city, fact, source = _text_field(fields, "city"), _text_field(fields, "fact"), _text_field(fields, "source")
        fetched_at = _read_time(fields, "fetched_at")
        if city is None or fact is None or source is None or fetched_at is None:
            continue
        records.append(CityFactRecord(city=city, fact=fact, source_url=source, fetched_at=fetched_at, agent=str(fields["agent"])))
    return tuple(records)


def mentions_user(texts: Iterable[str], user_id: str) -> bool:
    """True if any entry in `texts` is labelled with exactly this user."""
    return any(
        fields is not None and fields.get("user") == user_id for fields in (parse_line(line) for _, line in _lines(texts))
    )


# UNVERIFIED: which keys hold the stored text in a recall result (from the API reference, not seen on our tenant).
_TEXT_KEYS = ("answer", "text", "content", "method_return_value")


def result_texts(items: Iterable[Any]) -> tuple[str, ...]:
    """The text inside recall results or a session's QA list: a session item's `answer`, a graph item's `text`
    (a string or a list of chunks), a trace's `method_return_value`, a session-context item's `content`, or a
    bare string. An item with only a `question` gives the question. Shapes it does not know give nothing."""
    found: list[str] = []
    for item in items:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, Mapping):
            found.extend(_item_texts(item))
    return tuple(found)


def _item_texts(item: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    for key in _TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value)
        elif isinstance(value, list):
            found.extend(v for v in value if isinstance(v, str) and v.strip())
    question = item.get("question")
    if not found and isinstance(question, str) and question.strip():
        found.append(question)
    return found
