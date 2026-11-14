"""The plan's timed events (anchors and scheduled items) and the geometry between them.

An *event* is an anchor or a `PlanEntry` item that has a `when`. The stay is not an event: it has
no time, so a leg to or from `"stay"` is checked only against the event at its other end.
"""

import datetime as dt
import math
from dataclasses import dataclass
from enum import StrEnum

from plan_my_stay.enums import Seat
from plan_my_stay.models import When
from plan_my_stay.state import TripState
from plan_my_stay.timeutil import TRIP_TZ
from plan_my_stay.verifier.hours import weekday_key

STAY_ID = "stay"  # the leg endpoint that stands for the traveler's stay
_EARTH_RADIUS_KM = 6371.0088


class EventKind(StrEnum):
    ANCHOR = "anchor"
    ITEM = "item"


@dataclass(frozen=True)
class Event:
    id: str
    name: str
    kind: EventKind
    start: dt.datetime  # timezone-aware
    end: dt.datetime
    lat: float | None
    lng: float | None
    owner: Seat  # who revises it: the Concierge for an anchor, the item's agent otherwise


def when_to_interval(when: When) -> tuple[dt.datetime, dt.datetime]:
    """The item's `when` (a date and 24-hour clock times in the trip's time zone) as aware datetimes."""
    day = when.date
    start = dt.datetime.combine(day, dt.time.fromisoformat(when.start), tzinfo=TRIP_TZ)
    end = dt.datetime.combine(day, dt.time.fromisoformat(when.end), tzinfo=TRIP_TZ)
    return start, end


def build_events(state: TripState) -> tuple[Event, ...]:
    """Every anchor and every scheduled item, sorted by start (then end, then id)."""
    events = [
        Event(a.id, f"meeting {a.id}", EventKind.ANCHOR, a.start, a.end, a.lat, a.lng, Seat.CONCIERGE) for a in state.anchors
    ]
    for entry in state.entries:
        item = entry.item
        if item.when is None:
            continue
        start, end = when_to_interval(item.when)
        events.append(Event(item.item_id, item.name, EventKind.ITEM, start, end, item.lat, item.lng, entry.agent))
    return tuple(sorted(events, key=lambda e: (e.start, e.end, e.id)))


def event_index(events: tuple[Event, ...]) -> dict[str, Event]:
    """Events by id. If two events ever share an id, the earlier one is kept (a duplicate id is a bug upstream)."""
    index: dict[str, Event] = {}
    for event in events:
        index.setdefault(event.id, event)
    return index


def distance_km(a: Event, b: Event) -> float | None:
    """Great-circle distance between two events, or None when either has no coordinates."""
    if a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return None
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    d_lat, d_lng = lat2 - lat1, math.radians(b.lng - a.lng)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def overlaps(a: Event, b: Event) -> bool:
    """True when the two events share time. Touching (one ends as the other starts) is not an overlap."""
    return a.start < b.end and b.start < a.end


def fmt_time(moment: dt.datetime) -> str:
    """`Tue 18:30` in the trip's time zone (weekday names do not depend on the locale)."""
    local = moment.astimezone(TRIP_TZ)
    return f"{weekday_key(local.date())} {local:%H:%M}"


def fmt_span(event: Event) -> str:
    """`Tue 18:30-19:45`, or both ends in full when the event crosses midnight."""
    end = fmt_time(event.end)
    if event.start.astimezone(TRIP_TZ).date() == event.end.astimezone(TRIP_TZ).date():
        end = end.split(" ", 1)[1]
    return f"{fmt_time(event.start)}-{end}"
