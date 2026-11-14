"""Deterministic food re-planning over evidence gathered by source agents.

This worker deliberately does not scrape or invent restaurant facts. Search,
place details, routing, memory retrieval, and model inference are supplied by
adapters/orchestration; this module validates and ranks their structured output.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from typing import Any


CONTRACT = "food.replan.v1"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _distance_km(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    if not a or not b:
        return None
    try:
        lat1, lon1 = float(a["lat"]), float(a["lon"])
        lat2, lon2 = float(b["lat"]), float(b["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    earth_radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    haversine = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(min(1.0, haversine)))


def _group_requirements(travelers: list[dict[str, Any]]) -> set[str]:
    required: set[str] = set()
    for traveler in travelers:
        required.update(str(item) for item in traveler.get("hard_requirements", []))
    return required


def _preference_score(candidate: dict[str, Any], travelers: list[dict[str, Any]]) -> float:
    cuisines = {str(value).casefold() for value in candidate.get("cuisines", [])}
    if not travelers:
        return 0.0
    scores = []
    for traveler in travelers:
        preferred = {str(value).casefold() for value in traveler.get("preferred_cuisines", [])}
        disliked = {str(value).casefold() for value in traveler.get("disliked_cuisines", [])}
        if cuisines & disliked:
            scores.append(-1.0)
        elif preferred:
            scores.append(1.0 if cuisines & preferred else 0.0)
        else:
            scores.append(0.0)
    return sum(scores) / len(scores)


def _evaluate(candidate: dict[str, Any], slot: dict[str, Any], trip: dict[str, Any],
              travelers: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    evidence = candidate.get("evidence", {})
    required = _group_requirements(travelers)
    attributes = candidate.get("verified_attributes", {})
    missing = sorted(key for key in required if attributes.get(key) is not True)
    failed = sorted(key for key in required if attributes.get(key) is False)
    hard_ok = not missing and not failed

    anchor = slot.get("anchor") or trip.get("selected_stay", {}).get("location")
    distance = _distance_km(candidate.get("location"), anchor)
    max_distance = slot.get("max_distance_km")
    within_radius = distance is not None and (max_distance is None or distance <= float(max_distance))

    context_revision = int(trip.get("revision", 0))
    geo_revision = candidate.get("travel_estimate", {}).get("trip_revision")
    route = candidate.get("travel_estimate", {})
    route_current = geo_revision == context_revision and route.get("minutes") is not None
    menu_time = _parse_time(evidence.get("menu", {}).get("observed_at"))
    menu_age_hours = (now - menu_time).total_seconds() / 3600 if menu_time else None
    menu_max_age = float(trip.get("freshness_policy", {}).get("menu_max_age_hours", 720))
    menu_current = menu_age_hours is not None and 0 <= menu_age_hours <= menu_max_age

    hours = evidence.get("hours", {})
    hours_time = _parse_time(hours.get("observed_at"))
    hours_current = bool(hours_time and 0 <= (now - hours_time).total_seconds() / 3600 <=
                         float(trip.get("freshness_policy", {}).get("hours_max_age_hours", 168)))
    hours_status = hours.get("meal_fit", "unknown")
    confirmed_open = hours_current and hours_status in {"confirmed_open", "published_hours_match"}
    score = _preference_score(candidate, travelers)
    if distance is not None:
        score -= min(distance, 20.0) / 20.0
    if not menu_current:
        score -= 0.15
    if not confirmed_open:
        score -= 0.15

    refresh_reasons = []
    if not menu_current:
        refresh_reasons.append("menu_missing_or_stale")
    if not hours_current:
        refresh_reasons.append("hours_missing_or_stale_for_meal_date")
    elif hours_status == "unknown":
        refresh_reasons.append("meal_time_fit_unknown")
    if not route_current:
        refresh_reasons.append("travel_time_missing_or_from_old_trip_revision")

    reasons = []
    if failed:
        reasons.append("hard_requirements_failed:" + ",".join(failed))
    if missing:
        reasons.append("hard_requirements_unverified:" + ",".join(missing))
    if not within_radius:
        reasons.append("outside_or_unknown_radius")
    if hours_status == "closed" and hours_current:
        reasons.append("published_closed_for_meal_time")

    return {
        "restaurant_id": candidate.get("id"),
        "name": candidate.get("name"),
        "address": candidate.get("address"),
        "source_urls": sorted(set(evidence.get("source_urls", []))),
        "distance_km": round(distance, 2) if distance is not None else None,
        "travel_minutes": route.get("minutes") if route_current else None,
        "travel_time_status": "current_route_estimate" if route_current else "unverified",
        "menu_observed_at": evidence.get("menu", {}).get("observed_at"),
        "hours_observed_at": hours.get("observed_at"),
        "meal_time_status": hours_status if hours_current else "unknown_stale_or_unverified",
        "price_evidence": evidence.get("price", {}),
        "hard_requirements_passed": hard_ok,
        "missing_hard_requirements": missing,
        "failed_hard_requirements": failed,
        "preference_score": round(score, 3),
        "eligible_for_shortlist": hard_ok and within_radius and not (hours_current and hours_status == "closed"),
        "refresh_reasons": refresh_reasons,
        "exclusion_reasons": reasons,
    }


def replan(request: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Re-rank retained candidates against a new trip revision."""
    if request.get("type") != CONTRACT:
        raise ValueError(f"type must be {CONTRACT}")
    trip = request.get("trip")
    if not isinstance(trip, dict) or not trip.get("id"):
        raise ValueError("trip.id is required")
    travelers = request.get("travelers", [])
    slots = request.get("meal_slots", [])
    candidates = request.get("candidate_pool", [])
    now = now or datetime.now(timezone.utc)

    old_revision = request.get("previous_trip_revision")
    revision_changed = old_revision is not None and int(old_revision) != int(trip.get("revision", 0))
    change_event = request.get("change_event", {})
    results = []
    for slot in slots:
        evaluated = [_evaluate(candidate, slot, trip, travelers, now) for candidate in candidates]
        evaluated.sort(key=lambda item: (
            not item["eligible_for_shortlist"],
            -item["preference_score"],
            item["distance_km"] if item["distance_km"] is not None else float("inf"),
            item["name"] or "",
        ))
        results.append({
            "meal_slot_id": slot.get("id"),
            "date": slot.get("date"),
            "window": {"start": slot.get("start"), "end": slot.get("end")},
            "anchor": slot.get("anchor") or trip.get("selected_stay", {}).get("location"),
            "options": evaluated,
            "needs_discovery": len([item for item in evaluated if item["eligible_for_shortlist"]]) <
                               int(slot.get("minimum_options", 3)),
        })

    return {
        "type": "food.results.v1",
        "trip_id": trip["id"],
        "trip_revision": int(trip.get("revision", 0)),
        "worker": "food",
        "retained_candidate_count": len(candidates),
        "evidence_reused": bool(candidates),
        "replanned_after_revision_change": revision_changed,
        "geographic_calculations_recomputed": revision_changed,
        "trigger_event_type": change_event.get("type"),
        "meal_slots": results,
        "source_status": "candidate_pool_only; no live search or reservation check performed",
    }


def handle_context_change(event: dict[str, Any], request: dict[str, Any],
                          now: datetime | None = None) -> dict[str, Any]:
    """Accept BAND's trip event and run only when food is an affected agent.

    The coordinator provides the current authoritative snapshot and retained
    candidate pool in ``request``; event payloads alone are not enough to plan.
    """
    if event.get("type") != "trip.context_changed.v1":
        raise ValueError("event.type must be trip.context_changed.v1")
    if "food" not in event.get("affected_agents", []):
        return {
            "type": "food.replan.ignored.v1",
            "trip_id": event.get("trip_id"),
            "trip_revision": event.get("revision"),
            "reason": "food_not_in_affected_agents",
        }
    if request.get("trip", {}).get("id") != event.get("trip_id"):
        raise ValueError("event.trip_id must match request.trip.id")
    if int(request.get("trip", {}).get("revision", -1)) != int(event.get("revision", -2)):
        raise ValueError("request.trip.revision must match event.revision")
    request = dict(request)
    request["change_event"] = event
    request.setdefault("previous_trip_revision", int(event["revision"]) - 1)
    return replan(request, now=now)


def main() -> None:
    request = json.load(sys.stdin)
    print(json.dumps(replan(request), ensure_ascii=False))


if __name__ == "__main__":
    main()
