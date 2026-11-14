"""Turning parsed memory records into what `MemoryPort` returns. Pure functions, shared by `CogneeMemory`
and `InMemoryMemory` so the two cannot drift apart: newest value wins, strict per-user filtering, fresh facts only."""

import datetime as dt
import logging
import re
from collections.abc import Callable, Iterable
from typing import Any

from plan_my_stay.enums import Mode
from plan_my_stay.memory.text import CityFactRecord, PreferenceRecord, TripStateRecord
from plan_my_stay.models import Profile
from plan_my_stay.state import TripState

logger = logging.getLogger(__name__)

# Prices change within a day (nightly rates, class prices), so a price fact older than this is not used.
# Other facts (hours, neighborhood traits) have no expiry here: Cognee keeps them with their fetch time.
DEFAULT_PRICE_FACT_TTL = dt.timedelta(hours=24)
DEFAULT_FACT_LIMIT = 15

_OLDEST = dt.datetime.min.replace(tzinfo=dt.UTC)
_UNUSABLE: Any = object()
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WORD = re.compile(r"[a-z0-9]{3,}")
_PRICE = re.compile(
    r"[$€£]|\b(?:usd|eur|gbp)\b|\bpric(?:e|es|ed|ing)\b|\bcosts?\b|\bfees?\b|\brates?\b|\bper night\b|\bcheap\b|\bexpensive\b",
    re.IGNORECASE,
)


# --- profile -------------------------------------------------------------------------------


def build_profile(records: Iterable[PreferenceRecord], user_id: str) -> Profile:
    """The profile for `user_id`: the newest value per key, ignoring every other user's records."""
    newest: dict[str, PreferenceRecord] = {}
    for record in records:
        if record.user_id != user_id:
            continue
        current = newest.get(record.key)
        if current is None or (record.at or _OLDEST, record.seq) > (current.at or _OLDEST, current.seq):
            newest[record.key] = record
    data: dict[str, Any] = {}
    for key, record in newest.items():
        value = _COERCE.get(key, _keep)(key, record.value)
        if value is _UNUSABLE:
            logger.warning("Ignoring the remembered %r preference: its value has the wrong shape", key)
            continue
        data[key] = value
    return Profile.model_validate(data)


def _keep(_key: str, value: Any) -> Any:
    return value


def _strings(_key: str, value: Any) -> Any:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, list) or not all(_is_scalar(v) for v in value):
        return _UNUSABLE
    return tuple(dict.fromkeys(str(v) for v in value))


def _clock_times(key: str, value: Any) -> Any:
    items = _strings(key, value)
    if items is _UNUSABLE:
        return items
    valid = tuple(v for v in items if _CLOCK.match(v))
    if len(valid) != len(items):
        logger.warning("Ignoring remembered %r values that are not 24-hour HH:MM times", key)
    return valid


def _text_or_none(_key: str, value: Any) -> Any:
    if value is None:
        return None
    return (value.strip() or None) if isinstance(value, str) else _UNUSABLE


def _mode(_key: str, value: Any) -> Any:
    if value is None:
        return None
    try:
        return Mode(str(value).strip().lower()) if isinstance(value, str) else _UNUSABLE
    except ValueError:
        return _UNUSABLE


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


_COERCE: dict[str, Callable[[str, Any], Any]] = {
    "diet": _strings,
    "cuisines": _strings,
    "gym_types": _strings,
    "stay_preferences": _strings,
    "workout_times": _clock_times,
    "price_comfort": _text_or_none,
    "transport_mode": _mode,
}


# --- trip state ----------------------------------------------------------------------------


def latest_trip_state(records: Iterable[TripStateRecord], *, user_id: str, trip_id: str) -> TripState | None:
    """The copy with the highest `state_version` (the latest copy if two share it). A copy whose labels disagree
    with the state inside it is not trusted."""
    candidates = [
        r
        for r in records
        if r.user_id == user_id == r.state.user_id and r.trip_id == trip_id == r.state.trip_id
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.state.state_version, r.at or _OLDEST, r.seq)).state


# --- city facts ----------------------------------------------------------------------------


def is_price_fact(fact: str) -> bool:
    """True if the fact text talks about money, so it goes stale quickly."""
    return _PRICE.search(fact) is not None


def select_city_facts(
    records: Iterable[CityFactRecord],
    *,
    city: str,
    query: str,
    now: dt.datetime,
    price_ttl: dt.timedelta = DEFAULT_PRICE_FACT_TTL,
    limit: int = DEFAULT_FACT_LIMIT,
) -> tuple[CityFactRecord, ...]:
    """Facts for `city`, newest first: price facts older than `price_ttl` are dropped, the same fact from the
    same source is listed once (newest), and facts sharing a word with `query` are kept in preference to the
    rest (if none shares one, all of the city's facts are returned)."""
    wanted = _city_key(city)
    unique: dict[tuple[str, str], CityFactRecord] = {}
    for record in records:
        if _city_key(record.city) != wanted:
            continue
        if is_price_fact(record.fact) and now - record.fetched_at > price_ttl:
            continue
        key = (record.fact, record.source_url)
        if key not in unique or record.fetched_at > unique[key].fetched_at:
            unique[key] = record
    newest_first = sorted(unique.values(), key=lambda r: r.fetched_at, reverse=True)
    words = set(_WORD.findall(query.casefold()))
    relevant = [r for r in newest_first if words & set(_WORD.findall(r.fact.casefold()))]
    return tuple((relevant or newest_first)[:limit])


def _city_key(city: str) -> str:
    return " ".join(city.casefold().split())
