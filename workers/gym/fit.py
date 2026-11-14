"""Hard filters and fail-closed scoring for gym candidates.

Fail-closed philosophy (per the project's house style): never fabricate a
fact such as hours or price, and never let a missing fact silently count as
a pass. A candidate missing a fact stays eligible (absence isn't evidence of
mismatch) but is ranked below a candidate whose matching fact is confirmed,
and is tagged in `missing` so a caller (or, later, the Verifier) can see
exactly what wasn't checked.

Hard constraints (gym_type, hours) are plain code here, never an LLM guess --
docs/PLAN.md's own design keeps the Stay agent's ranking in plain code for
the same reason, and the SIE reranker is explicitly "not reasoning about
opening times" (its own doc note: hard constraints are code filters on
extracted hours).
"""

from __future__ import annotations

from typing import Any

from workers.gym.slots import TRIP_TZ, TimeRange

_WEEKDAY_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _hours_cover(hours_for_day: str, window: TimeRange) -> bool:
    """True if a single "HH:MM-HH:MM" (or several, comma separated) range
    covers `window`'s start and end clock times. Only compares time-of-day;
    the caller already knows `window` falls on the relevant day."""
    for period in hours_for_day.split(","):
        period = period.strip()
        if "-" not in period:
            continue
        start_s, end_s = period.split("-", 1)
        try:
            start_h, start_m = (int(x) for x in start_s.strip().split(":"))
            end_h, end_m = (int(x) for x in end_s.strip().split(":"))
        except ValueError:
            continue
        window_start_minutes = window.start.hour * 60 + window.start.minute
        window_end_minutes = window.end.hour * 60 + window.end.minute
        period_start_minutes = start_h * 60 + start_m
        period_end_minutes = end_h * 60 + end_m
        if period_start_minutes <= window_start_minutes and window_end_minutes <= period_end_minutes:
            return True
    return False


def score_candidate(candidate: dict[str, Any], profile: dict[str, Any], window: TimeRange) -> dict[str, Any]:
    """Hard-filter one candidate and note which facts were confirmed.

    Returns {"eligible": bool, "reason": str, "missing": list[str]}.
    """
    missing: list[str] = []

    gym_type = candidate.get("gym_type")
    wanted_type = profile.get("gym_type")
    if gym_type is not None and wanted_type is not None:
        if wanted_type not in gym_type:
            return {
                "eligible": False,
                "reason": f"gym_type {gym_type!r} does not include preferred {wanted_type!r}",
                "missing": [],
            }
    elif wanted_type is not None:
        missing.append("gym_type")

    hours = candidate.get("hours")
    if hours is not None:
        day_key = _WEEKDAY_KEYS[window.start.weekday()]
        hours_for_day = hours.get(day_key)
        if hours_for_day is None:
            missing.append("hours")
        elif not _hours_cover(hours_for_day, window):
            return {
                "eligible": False,
                "reason": f"hours {hours_for_day!r} on {day_key} do not cover {window.start.strftime('%H:%M')}-{window.end.strftime('%H:%M')}",
                "missing": [],
            }
    else:
        missing.append("hours")

    if missing:
        reason = "eligible; unconfirmed: " + ", ".join(missing)
    else:
        reason = "matches gym_type and hours cover the requested window"

    return {"eligible": True, "reason": reason, "missing": missing}


def rank_candidates(
    candidates: list[dict[str, Any]], profile: dict[str, Any], window: TimeRange
) -> dict[str, list[dict[str, Any]]]:
    """Rank candidates, confirmed-fit first, then missing-fact, drop ineligible.

    Returns {"ranked": [...], "excluded": [...]}. Each entry in "ranked" and
    "excluded" is the original candidate dict plus a "_fit" key holding
    score_candidate's result, so a caller (including a future zone_fit
    "which top items were excluded and why" use) can inspect the reason
    without re-scoring.
    """
    ranked: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for candidate in candidates:
        fit = score_candidate(candidate, profile, window)
        annotated = {**candidate, "_fit": fit}
        if fit["eligible"]:
            ranked.append(annotated)
        else:
            excluded.append(annotated)

    ranked.sort(key=lambda c: 1 if c["_fit"]["missing"] else 0)

    return {"ranked": ranked, "excluded": excluded}


def build_item(
    candidate: dict[str, Any],
    *,
    item_id: str,
    slot_id: str,
    date: str,
    when: TimeRange,
    model: str = "fixture",
) -> dict[str, Any]:
    """Assemble one proposal item per docs/PLAN.md's item shape.

    `location_precision` is "exact" when the fixture gives a full street
    address, else "zone" (fail-closed: never claim exact when we only know
    the neighborhood). When `model == "fixture"`, `fit.reason` says so
    explicitly, matching docs/PLAN.md's "a cached snapshot, labelled as
    cached" fallback language -- a reader must never mistake fixture data
    for a live result.
    """
    address = candidate.get("address")
    has_street_address = isinstance(address, dict) and bool(address.get("street"))
    location_precision = "exact" if has_street_address else "zone"

    fit = candidate.get("_fit") or {"reason": "not scored", "profile_fields": []}
    base_reason = fit.get("reason", "")
    if model == "fixture":
        reason = f"{base_reason} (cached fixture data)"
    else:
        reason = base_reason

    return {
        "item_id": item_id,
        "slot_id": slot_id,
        "name": candidate.get("name"),
        "address": address,
        "location_precision": location_precision,
        "lat": candidate.get("lat"),
        "lng": candidate.get("lng"),
        "when": {
            "date": date,
            "start": when.start.strftime("%H:%M"),
            "end": when.end.strftime("%H:%M"),
        },
        "cost": candidate.get("cost", {"currency": "USD", "amount": None, "basis": "unknown"}),
        "fit": {
            "reason": reason,
            "profile_fields": ["gym_type", "preferred_workout_time"],
        },
        "hours": candidate.get("hours"),
        "source": candidate.get("source", {"url": None, "fetched_at": None}),
        "reader": {"model": model, "escalated": False},
        "dedupe_key": candidate.get("dedupe_key") or candidate.get("name", item_id),
    }
