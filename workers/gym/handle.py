"""The Gym agent's one integration point: handle(message) -> list[dict].

This is the function docs/PLAN.md asks every seat to expose ("Build each
agent as a plain function that takes a parsed message and returns the
messages to send"). BAND's shared band_adapter module (not built yet) will
call parse_message, then this, then post whatever comes back. Until that
adapter exists, this worker can be driven directly in tests or a REPL with
plain dicts.

Routing rules enforced here (from docs/PLAN.md's "Rules that keep every
message parseable"):
  - A malformed envelope gets exactly one error reply, never an exception.
  - An error message never gets answered with another error.
  - An unrecognized type gets an error (unknown major version or type is
    the one thing every receiver must reject).
  - A recognized type this seat doesn't own gets no reply (band_no_reply),
    not an error -- that's the normal "not for me" case, not a failure.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from workers.gym import envelope, fit, fixtures, slots, state
from workers.gym.slots import TimeRange

GYM_HANDLED_TYPES = {"request", "stay_pick", "conflict", "preference_learned", "route_reply"}

DEFAULT_SESSION_MINUTES = 45
DEFAULT_BUFFER_MINUTES = 30


def handle(
    message: dict[str, Any],
    *,
    discover: Callable[[dict, dict], list[dict]] | None = None,
    fetch_and_classify: Callable[[dict, dict], dict] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> list[dict[str, Any]]:
    discover = discover or fixtures.discover_gyms
    fetch_and_classify = fetch_and_classify or fixtures.fetch_and_classify
    clock = clock or (lambda: datetime.now(slots.TRIP_TZ))

    problems = envelope.validate_incoming(message)
    if problems:
        return [envelope.error_for(message, "invalid_envelope", problems)]

    if message["type"] == "error":
        return []

    if message["type"] not in envelope.KNOWN_TYPES:
        return [envelope.error_for(message, "unknown_type", ["type"])]

    if message["type"] not in GYM_HANDLED_TYPES:
        return []

    dispatch: dict[str, Callable[..., list[dict[str, Any]]]] = {
        "request": _handle_request,
        "stay_pick": _handle_stay_pick,
        "conflict": _handle_conflict,
        "preference_learned": _handle_preference_learned,
        "route_reply": _handle_route_reply,
    }
    return dispatch[message["type"]](message, discover, fetch_and_classify, clock)


# ---------------------------------------------------------------------------
# Helpers


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "gym"


def _anchor_time_range(anchor: dict[str, Any]) -> TimeRange:
    return TimeRange(
        start=datetime.fromisoformat(anchor["start"]).astimezone(slots.TRIP_TZ),
        end=datetime.fromisoformat(anchor["end"]).astimezone(slots.TRIP_TZ),
    )


def _anchor_date(anchor: dict[str, Any]) -> str:
    return datetime.fromisoformat(anchor["start"]).astimezone(slots.TRIP_TZ).date().isoformat()


def _group_anchors_by_date(anchors: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for anchor in anchors:
        grouped.setdefault(_anchor_date(anchor), []).append(anchor)
    return grouped


def _point_in_bounds(lat: float, lng: float, zone: dict[str, Any]) -> bool:
    bounds = zone.get("bounds")
    if not bounds:
        return False
    ne, sw = bounds["ne"], bounds["sw"]
    return sw["lat"] <= lat <= ne["lat"] and sw["lng"] <= lng <= ne["lng"]


def _zone_for_point(lat: float, lng: float, zones: list[dict[str, Any]]) -> dict[str, Any]:
    for zone in zones:
        if _point_in_bounds(lat, lng, zone):
            return zone
    return zones[0] if zones else {"id": None, "name": None}


def _slot_id_for_date(date: str, slots_payload: list[dict[str, Any]]) -> str:
    for slot in slots_payload:
        if slot.get("kind") == "gym" and slot.get("date") == date:
            return slot["id"]
    return f"gym-{date}"


def _bordering_anchor(slot: TimeRange, anchors_for_date: list[dict[str, Any]]) -> dict[str, Any]:
    """The meeting the picked slot sits next to: the latest meeting that
    ends at or before the slot's start (the trailing-gap and between-gap
    case), or, failing that, the earliest meeting that starts at or after
    the slot's end."""
    before = [a for a in anchors_for_date if _anchor_time_range(a).end <= slot.start]
    if before:
        return max(before, key=lambda a: _anchor_time_range(a).end)
    after = [a for a in anchors_for_date if _anchor_time_range(a).start >= slot.end]
    if after:
        return min(after, key=lambda a: _anchor_time_range(a).start)
    return anchors_for_date[0]


def _discover_and_rank(
    zone: dict[str, Any],
    profile: dict[str, Any],
    window: TimeRange,
    discover: Callable[[dict, dict], list[dict]],
    fetch_and_classify: Callable[[dict, dict], dict],
    context: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    raw_candidates = discover(zone, context)
    classified = [fetch_and_classify(c, profile) for c in raw_candidates]
    return fit.rank_candidates(classified, profile, window)


def _build_item_messages(
    *,
    candidate: dict[str, Any],
    date: str,
    slot_id: str,
    window: TimeRange,
    trip_id: str,
    user_id: str,
    state_version: int,
    reply_to: str | None,
    place_summary: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build the item plus its proposal and workout_slot messages."""
    item_id = f"gym-{_slug(candidate.get('name', 'gym'))}-{date}"
    item = fit.build_item(
        candidate, item_id=item_id, slot_id=slot_id, date=date, when=window, model="fixture"
    )

    workout_slot = envelope.build_message(
        type="workout_slot",
        from_="gym",
        to=["restaurant", "concierge"],
        trip_id=trip_id,
        user_id=user_id,
        state_version=state_version,
        reply_to=reply_to,
        summary=f"Workout on {date} from {window.start.strftime('%H:%M')} near {place_summary}",
        payload={
            "date": date,
            "start": window.start.strftime("%H:%M"),
            "end": window.end.strftime("%H:%M"),
            "place": {
                "item_id": item_id,
                "name": item["name"],
                "lat": item["lat"],
                "lng": item["lng"],
            },
        },
    )

    proposal = envelope.build_message(
        type="proposal",
        from_="gym",
        to=["concierge", "verifier"],
        trip_id=trip_id,
        user_id=user_id,
        state_version=state_version,
        reply_to=reply_to,
        summary=f"1 gym option for {date}",
        payload={"agent": "gym", "items": [item], "usage": {"calls": 0, "credits": 0}},
    )

    return item, proposal, workout_slot


# ---------------------------------------------------------------------------
# request


def _handle_request(message, discover, fetch_and_classify, clock) -> list[dict[str, Any]]:
    trip_id = message["trip_id"]
    user_id = message["user_id"]
    state_version = message["state_version"]
    payload = message["payload"]

    check_in = payload["dates"]["check_in"]
    anchors = payload.get("anchors", [])
    zones = payload.get("zones", [])
    profile = payload.get("profile", {})
    slots_payload = payload.get("slots", [])

    trip_state = state.get(trip_id)
    trip_state["profile"] = {**trip_state["profile"], **profile}

    by_date = _group_anchors_by_date(anchors)

    preferred_time_str = profile.get("preferred_workout_time", "07:00")
    preferred_time = datetime.strptime(preferred_time_str, "%H:%M").time()

    messages: list[dict[str, Any]] = []

    # --- Check-in date: compute time AND location, send proposal + workout_slot now.
    checkin_anchors = by_date.get(check_in, [])
    meetings = [_anchor_time_range(a) for a in checkin_anchors]
    window = slots.day_window(check_in, slots_payload)
    gaps = slots.free_gaps(window, meetings, buffer_minutes=DEFAULT_BUFFER_MINUTES)
    eligible = slots.eligible_gaps(gaps, check_in, check_in)
    chosen_slot = slots.pick_time(eligible, preferred_time, session_minutes=DEFAULT_SESSION_MINUTES)

    checkin_slot_id = _slot_id_for_date(check_in, slots_payload)

    if chosen_slot is not None and checkin_anchors:
        border = _bordering_anchor(chosen_slot, checkin_anchors)
        zone = _zone_for_point(border["lat"], border["lng"], zones)

        ranking = _discover_and_rank(
            zone,
            trip_state["profile"],
            chosen_slot,
            discover,
            fetch_and_classify,
            context={"profile": trip_state["profile"], "anchor": {"lat": border["lat"], "lng": border["lng"]}},
        )

        if ranking["ranked"]:
            top = ranking["ranked"][0]
            item, proposal, workout_slot = _build_item_messages(
                candidate=top,
                date=check_in,
                slot_id=checkin_slot_id,
                window=chosen_slot,
                trip_id=trip_id,
                user_id=user_id,
                state_version=state_version,
                reply_to=message["message_id"],
                place_summary=f"meeting {border.get('id', '')}".strip(),
            )
            trip_state["items"][item["item_id"]] = {
                "item": item,
                "zone_id": zone.get("id"),
                "date": check_in,
                "slot_id": checkin_slot_id,
                "window": chosen_slot,
                "excluded": [],
            }
            trip_state["final_dates"].add(check_in)
            messages.append(proposal)
            messages.append(workout_slot)

    # --- Every other date: compute time only; defer location to stay_pick.
    for date, date_anchors in by_date.items():
        if date == check_in:
            continue
        day_meetings = [_anchor_time_range(a) for a in date_anchors]
        day_win = slots.day_window(date, slots_payload)
        day_gaps = slots.free_gaps(day_win, day_meetings, buffer_minutes=DEFAULT_BUFFER_MINUTES)
        day_eligible = slots.eligible_gaps(day_gaps, date, check_in)
        day_slot = slots.pick_time(day_eligible, preferred_time, session_minutes=DEFAULT_SESSION_MINUTES)
        if day_slot is not None:
            trip_state["pending"][date] = {
                "slot": day_slot,
                "slot_id": _slot_id_for_date(date, slots_payload),
            }

    # --- zone_fit: rank each requested zone against the pending date's
    # window (falling back to the check-in date's window if nothing is
    # pending, e.g. a one-day trip).
    fit_window = None
    if trip_state["pending"]:
        fit_window = next(iter(trip_state["pending"].values()))["slot"]
    elif chosen_slot is not None:
        fit_window = chosen_slot

    zone_fit_messages: list[dict[str, Any]] = []
    if fit_window is not None:
        for zone in zones:
            ranking = _discover_and_rank(
                zone,
                trip_state["profile"],
                fit_window,
                discover,
                fetch_and_classify,
                context={"profile": trip_state["profile"], "zone": zone},
            )
            top_items = [
                {
                    "name": c.get("name"),
                    "lat": c.get("lat"),
                    "lng": c.get("lng"),
                    "can_host_preferred_slot": "hours" not in c["_fit"]["missing"],
                }
                for c in ranking["ranked"][:3]
            ]
            zone_fit_messages.append(
                envelope.build_message(
                    type="zone_fit",
                    from_="gym",
                    to=["stay"],
                    trip_id=trip_id,
                    user_id=user_id,
                    state_version=state_version,
                    reply_to=message["message_id"],
                    summary=f"{len(ranking['ranked'])} gym option(s) in zone {zone.get('id')}",
                    payload={
                        "zone_id": zone.get("id"),
                        "counts": {
                            "gyms_matching": len(ranking["ranked"]),
                            "gyms_total": len(ranking["ranked"]) + len(ranking["excluded"]),
                        },
                        "top_items": top_items,
                        "can_host_preferred_slot": any(i["can_host_preferred_slot"] for i in top_items),
                    },
                )
            )

    return [*zone_fit_messages, *messages]


# ---------------------------------------------------------------------------
# stay_pick


def _handle_stay_pick(message, discover, fetch_and_classify, clock) -> list[dict[str, Any]]:
    trip_id = message["trip_id"]
    user_id = message["user_id"]
    state_version = message["state_version"]
    payload = message["payload"]

    trip_state = state.get(trip_id)
    if not trip_state["pending"]:
        return []

    profile = trip_state["profile"]
    stay_lat, stay_lng = payload["lat"], payload["lng"]
    zone = {"id": payload.get("zone_id")}

    messages: list[dict[str, Any]] = []

    for date, pending_info in list(trip_state["pending"].items()):
        window: TimeRange = pending_info["slot"]
        slot_id = pending_info["slot_id"]

        ranking = _discover_and_rank(
            zone,
            profile,
            window,
            discover,
            fetch_and_classify,
            context={"profile": profile, "stay": {"lat": stay_lat, "lng": stay_lng}},
        )

        if not ranking["ranked"]:
            continue

        top = ranking["ranked"][0]
        item, proposal, workout_slot = _build_item_messages(
            candidate=top,
            date=date,
            slot_id=slot_id,
            window=window,
            trip_id=trip_id,
            user_id=user_id,
            state_version=state_version,
            reply_to=message["message_id"],
            place_summary="the stay",
        )

        trip_state["items"][item["item_id"]] = {
            "item": item,
            "zone_id": zone.get("id"),
            "date": date,
            "slot_id": slot_id,
            "window": window,
            "excluded": [],
        }
        trip_state["final_dates"].add(date)
        del trip_state["pending"][date]

        messages.append(proposal)
        messages.append(workout_slot)

    return messages


# ---------------------------------------------------------------------------
# conflict


def _handle_conflict(message, discover, fetch_and_classify, clock) -> list[dict[str, Any]]:
    trip_id = message["trip_id"]
    user_id = message["user_id"]
    state_version = message["state_version"]
    item_id = message["payload"]["item_id"]

    trip_state = state.get(trip_id)
    stored = trip_state["items"].get(item_id)
    if stored is None:
        return [envelope.error_for(message, "unknown_item", ["item_id"])]

    profile = trip_state["profile"]
    date = stored["date"]
    slot_id = stored["slot_id"]
    window: TimeRange = stored["window"]
    zone = {"id": stored["zone_id"]}

    # Exclude the disputed candidate by name (fixtures have no stable
    # source id yet; name is the best available identity for P0) plus any
    # candidate already excluded by a prior round on this same item chain.
    excluded_names = set(stored.get("excluded", [])) | {stored["item"]["name"]}

    raw_candidates = discover(zone, context={"profile": profile})
    classified = [fetch_and_classify(c, profile) for c in raw_candidates]
    remaining = [c for c in classified if c.get("name") not in excluded_names]
    ranking = fit.rank_candidates(remaining, profile, window)

    if not ranking["ranked"]:
        return [envelope.error_for(message, "no_eligible_candidate", ["item_id"])]

    top = ranking["ranked"][0]
    new_item_id = f"gym-{_slug(top.get('name', 'gym'))}-{date}"
    new_item = fit.build_item(
        top, item_id=new_item_id, slot_id=slot_id, date=date, when=window, model="fixture"
    )

    trip_state["items"][new_item_id] = {
        "item": new_item,
        "zone_id": zone.get("id"),
        "date": date,
        "slot_id": slot_id,
        "window": window,
        "excluded": list(excluded_names),
    }

    proposal = envelope.build_message(
        type="proposal",
        from_="gym",
        to=["concierge", "verifier"],
        trip_id=trip_id,
        user_id=user_id,
        state_version=state_version,
        reply_to=message["message_id"],
        summary=f"Replaced disputed gym option for {date}",
        payload={"agent": "gym", "items": [new_item], "usage": {"calls": 0, "credits": 0}},
    )

    # Same slot_id/window means the date/time did not change, so no new
    # workout_slot is needed -- only the place behind the existing slot did.
    return [proposal]


# ---------------------------------------------------------------------------
# preference_learned


def _handle_preference_learned(message, discover, fetch_and_classify, clock) -> list[dict[str, Any]]:
    trip_id = message["trip_id"]
    payload = message["payload"]
    trip_state = state.get(trip_id)
    trip_state["profile"][payload["key"]] = payload["value"]
    return []


# ---------------------------------------------------------------------------
# route_reply (stub; route_request/route_reply wiring is P1+)


def _handle_route_reply(message, discover, fetch_and_classify, clock) -> list[dict[str, Any]]:
    return []
