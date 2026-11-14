"""The trip as a time-ordered list of events: anchors and the specialists' chosen items.

Shared by the leg planner (which legs to ask Routing for) and the itinerary (the day-by-day listing).
"""

import datetime as dt
from dataclasses import dataclass

from plan_my_stay.models import Anchor, PlanEntry, ProposalItem
from plan_my_stay.state import TripState
from plan_my_stay.timeutil import TRIP_TZ


@dataclass(frozen=True)
class Event:
    """A fixed meeting (anchor) or a chosen item with a time. `lat` and `lng` are None when only a zone is known."""

    id: str
    start: dt.datetime
    end: dt.datetime
    lat: float | None
    lng: float | None
    anchor: Anchor | None = None
    entry: PlanEntry | None = None


def item_interval(item: ProposalItem) -> tuple[dt.datetime, dt.datetime] | None:
    """The item's start and end as aware datetimes in the trip's time zone, or None if it has no `when`."""
    when = item.when
    if when is None:
        return None

    def at(clock: str) -> dt.datetime:
        return dt.datetime.combine(when.date, dt.time.fromisoformat(clock), tzinfo=TRIP_TZ)

    return at(when.start), at(when.end)


def build_events(state: TripState) -> tuple[Event, ...]:
    """Anchors and timed items, sorted by start (then id, so the order is stable)."""
    events = [Event(a.id, a.start, a.end, a.lat, a.lng, anchor=a) for a in state.anchors]
    for entry in state.entries:
        interval = item_interval(entry.item)
        if interval is not None:
            events.append(
                Event(entry.item.item_id, interval[0], interval[1], entry.item.lat, entry.item.lng, entry=entry)
            )
    return tuple(sorted(events, key=lambda event: (event.start, event.id)))
