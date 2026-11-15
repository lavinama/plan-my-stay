"""One valid example of every message type, and a few invalid ones (docs/PLAN.md, "Contract tests").

`valid_examples()` returns canonical JSON dicts: each was parsed and re-serialized, so it
is exactly what `serialize()` produces. Each agent's tests round-trip them through
`parse_message`, and each stub answers with them first. The same data is exported as JSON
files by scripts/export_contracts.py (see `contract_examples/`).
"""

import copy
import json
from typing import Any

from plan_my_stay import demo
from plan_my_stay.messages.envelope import parse_message, serialize
from plan_my_stay.messages.errors import ErrorCode

_T = "2026-11-14T16:05:00-05:00"
_ID = "00000000-0000-4000-8000-{:012d}".format
_HASH = "9f2c1b7e4a6d3c85b0e1f2a3d4c5b6a79881726354a3b2c1d0e9f8a7b6c5d4e3"


def _envelope(n: int, type_: str, sender: str, to: list[str], summary: str, payload: dict[str, Any], **extra: Any) -> dict:
    return {
        "schema_version": "1.0",
        "type": type_,
        "message_id": _ID(n),
        "reply_to": extra.get("reply_to"),
        "trip_id": demo.TRIP_ID,
        "user_id": demo.USER_ID,
        "state_version": extra.get("state_version", 2),
        "from": sender,
        "to": to,
        "sent_at": _T,
        "summary": summary,
        "payload": payload,
    }


# The plan's own illustrative request and proposal, verbatim.
PLAN_REQUEST = {
    "schema_version": "1.0",
    "type": "request",
    "message_id": "6f1c2a52-6d0e-4c1b-9a53-1f0f6b2a9e10",
    "reply_to": None,
    "trip_id": "7c3f1d2e-5a4b-4e8a-9b6d-2f1c0a8e4b73",
    "user_id": "demo-user-1",
    "state_version": 2,
    "from": "concierge",
    "to": ["restaurant"],
    "sent_at": "2026-11-14T16:05:00-05:00",
    "summary": "Find vegetarian dinner options for Tue 17 Nov",
    "payload": {
        "city": "New York, NY",
        "dates": {"check_in": "2026-11-17", "check_out": "2026-11-19"},
        "guests": 1,
        "anchors": [
            {"id": "A", "lat": 40.7066, "lng": -74.0053, "start": "2026-11-17T11:00:00-05:00", "end": "2026-11-17T12:00:00-05:00", "counts_for_stay": False},
            {"id": "B", "lat": 40.7502, "lng": -73.9987, "start": "2026-11-17T16:00:00-05:00", "end": "2026-11-17T17:00:00-05:00", "counts_for_stay": True},
        ],
        "zones": [{"id": "z1", "name": "Chelsea", "bounds": {"ne": {"lat": 40.756, "lng": -73.99}, "sw": {"lat": 40.738, "lng": -74.01}}}],
        "slots": [{"id": "s1", "kind": "dinner", "date": "2026-11-17", "window": {"start": "17:30", "end": "21:00"}}],
        "profile": {"diet": ["vegetarian"], "cuisines": ["Mediterranean", "Indian"]},
        "constraints": {"max_walk_minutes": 15},
        "response_type": "proposal",
    },
}

PLAN_PROPOSAL = {
    "schema_version": "1.0",
    "type": "proposal",
    "message_id": "b7d0c9ae-3f0a-4e43-8d1e-3a4a7b3f5c21",
    "reply_to": "6f1c2a52-6d0e-4c1b-9a53-1f0f6b2a9e10",
    "trip_id": "7c3f1d2e-5a4b-4e8a-9b6d-2f1c0a8e4b73",
    "user_id": "demo-user-1",
    "state_version": 2,
    "from": "restaurant",
    "to": ["concierge", "verifier"],
    "sent_at": "2026-11-14T16:07:12-05:00",
    "summary": "1 dinner option for Tue 17 Nov in zone z1",
    "payload": {
        "agent": "restaurant",
        "items": [
            {
                "item_id": "rest-urban-vegan-kitchen",
                "slot_id": "s1",
                "name": "Urban Vegan Kitchen",
                "address": {"street": "265 W 23rd St", "city": "New York", "zip": "10011"},
                "lat": 40.7445,
                "lng": -73.9983,
                "when": {"date": "2026-11-17", "start": "18:30", "end": "19:45"},
                "cost": {"currency": "USD", "amount": None, "basis": "unknown"},
                "fit": {"reason": "Fully vegan menu; open until 22:00 on Tuesday", "profile_fields": ["diet"]},
                "hours": {"Tue": "11:00-22:00"},
                "source": {"url": "https://urbanvegankitchen.com", "fetched_at": "2026-11-14T16:06:40-05:00"},
                "reader": {"model": "Qwen/Qwen3.5-4B", "escalated": False},
                "dedupe_key": "urban-vegan-kitchen|10011",
            }
        ],
        "usage": {"calls": 3, "credits": 118},
    },
}

_COMMUTE = {"B": {"walking": 21, "transit": 12, "driving": 9}, "C": {"walking": 38, "transit": 22, "driving": 14}}


def _raw_examples() -> dict[str, dict[str, Any]]:
    return {
        "request": PLAN_REQUEST,
        "proposal": PLAN_PROPOSAL,
        "plan_for_review": _envelope(
            13, "proposal", "concierge", ["verifier"], "Merged plan for review (state_version 3)",
            {"agent": "concierge", "items": [], "usage": {"calls": 0, "credits": 0},
             "state": demo.demo_state_with_plan().model_dump(mode="json")},
            state_version=3,
        ),
        "stay_request": _envelope(
            17, "request", "concierge", ["stay"], "Find stays in New York, NY, Tue 17 Nov to Thu 19 Nov within $1,000.00",
            {"city": demo.CITY, "dates": {"check_in": "2026-11-17", "check_out": "2026-11-19"}, "guests": 1,
             "meetings": [{"id": a.id, "address": demo.MEETING_ADDRESSES[a.id], "start": a.start.isoformat(),
                           "end": a.end.isoformat(), "counts_for_stay": a.counts_for_stay} for a in demo.demo_anchors()],
             "zones": [z.model_dump(mode="json") for z in demo.demo_zones()],
             "profile": {"stay_preferences": ["quiet street", "private bathroom"]},
             "constraints": {"max_walk_minutes": 15}, "budget": {"currency": "USD", "amount": 1000},
             "roster": ["stay"], "round": 0, "response_type": "stay_shortlist"},
        ),
        "zone_fit": _envelope(
            3, "zone_fit", "restaurant", ["stay"], "Chelsea: 7 vegetarian options within a short walk",
            {"zone_id": "z1", "counts": {"restaurants": 7, "gyms": None},
             "top_items": [{"item_id": "rest-urban-vegan-kitchen", "name": "Urban Vegan Kitchen", "walk_minutes": 6, "fits": True}]},
            reply_to=PLAN_REQUEST["message_id"],
        ),
        "stay_shortlist": _envelope(
            4, "stay_shortlist", "stay", ["concierge"], "2 stays within the $1,000 budget; Chelsea ranks first on commute",
            {"stays": [
                {"rank": 1, "deciding_factor": "commute", "stay_id": "stay-chelsea-1", "zone_id": "z1", "name": "Quiet Chelsea studio",
                 "total_price": 640, "currency": "USD", "cost_basis": "card_total_fees_unknown", "commute_minutes": _COMMUTE,
                 "average_commute_minutes": 11.5},
                {"rank": 2, "deciding_factor": "gym_fit", "stay_id": "stay-tribeca-1", "zone_id": "z2", "name": "TriBeCa loft",
                 "total_price": 780, "currency": "USD", "cost_basis": "total_incl_fees",
                 "commute_minutes": {"B": {"walking": 48, "transit": 24, "driving": 17}, "C": {"walking": 15, "transit": 11, "driving": 8}},
                 "average_commute_minutes": 12.5},
            ]},
        ),
        "stay_pick": _envelope(
            5, "stay_pick", "stay", ["restaurant", "gym", "concierge"], "Stay pick: Chelsea studio",
            {"stay_id": "stay-chelsea-1", "zone_id": "z1", "lat": None, "lng": None, "commute_minutes": _COMMUTE},
        ),
        "workout_slot": _envelope(
            6, "workout_slot", "gym", ["restaurant", "concierge"], "Workout Tue 17 Nov 17:30 to 18:15 at Example HIIT Studio",
            {"date": "2026-11-17", "start": "17:30", "end": "18:15",
             "place": {"name": "Example HIIT Studio", "address": {"street": "1 Example Plaza", "city": "New York", "zip": "10001"},
                       "lat": 40.7488, "lng": -73.9971}},
        ),
        "preference_learned": _envelope(
            7, "preference_learned", "restaurant", ["concierge", "gym"], "Learned: the user eats early",
            {"key": "meal_timing", "value": "early", "evidence": "Rejected two 21:00 reservations, accepted 18:00"},
        ),
        "conflict": _envelope(
            8, "conflict", "verifier", ["restaurant"], "Urban Vegan Kitchen closes before the dinner ends",
            {"rule": "open_during_slot", "item_id": "rest-urban-vegan-kitchen",
             "detail": "Dinner runs 19:30 to 20:45 but the kitchen closes at 20:00", "suggestion": "Pick a place open until at least 21:00"},
        ),
        "verdict": _envelope(
            9, "verdict", "verifier", ["concierge"], "Plan cleared (1 warning)",
            {"status": "warn", "plan_hash": _HASH,
             "conflicts": [{"rule": "fee_basis_unknown", "item_id": "stay-chelsea-1",
                            "detail": "Fee basis unknown; a 15% reserve was added", "suggestion": None}]},
            state_version=3,
        ),
        "error": _envelope(
            10, "error", "restaurant", ["concierge"], "Rejected a message: validation_failed",
            {"code": "validation_failed", "bad_message_id": PLAN_REQUEST["message_id"], "fields": ["payload.anchors"]},
            reply_to=PLAN_REQUEST["message_id"],
        ),
        "route_request": _envelope(
            11, "route_request", "stay", ["routing"], "Route: Chelsea zone to meeting B at 16:00",
            {"origin": {"lat": 40.7465, "lng": -74.0014}, "destination": {"address": "368 9th Ave, New York, NY 10001"},
             "depart_at": "2026-11-17T15:30:00-05:00", "modes": ["walking", "transit", "driving"]},
        ),
        "hotel_query": _envelope(
            14, "hotel_query", "concierge", ["gym", "restaurant"],
            "Measure from 2 hotels: nearest gym, restaurants within a 10-minute walk",
            {"hotels": [
                {"stay_id": "stay-chelsea-1", "zone_id": "z1", "name": "Quiet Chelsea studio", "lat": 40.7465, "lng": -74.0014,
                 "location_precision": "exact"},
                {"stay_id": "stay-tribeca-1", "zone_id": "z2", "name": "TriBeCa loft", "lat": 40.7194, "lng": -74.0055,
                 "location_precision": "zone"},
            ], "radius_minutes": 10},
        ),
        "gym_fit": _envelope(
            15, "gym_fit", "gym", ["concierge"], "Nearest gym: 150 m from Chelsea, none near TriBeCa",
            {"hotels": [
                {"stay_id": "stay-chelsea-1", "nearest_gym": {"item_id": "gym-example-hiit", "name": "Example HIIT Studio",
                                                              "distance_m": 150.5, "walk_minutes": 2.5, "lat": 40.7488, "lng": -73.9971}},
                {"stay_id": "stay-tribeca-1", "nearest_gym": None},
            ]},
            reply_to=_ID(14),
        ),
        "restaurant_fit": _envelope(
            16, "restaurant_fit", "restaurant", ["concierge"], "Restaurants within 10 min: 3 near Chelsea (avg rank 2.7), 0 near TriBeCa",
            {"radius_minutes": 10, "hotels": [
                {"stay_id": "stay-chelsea-1", "count": 3, "average_rank": 2.67,
                 "top": [{"item_id": "rest-urban-vegan-kitchen", "name": "Urban Vegan Kitchen", "rank": 1, "walk_minutes": 6.0},
                         {"name": "Cafe B", "rank": 3, "walk_minutes": 8.0}, {"name": "Cafe C", "rank": 4, "walk_minutes": 9.5}]},
                {"stay_id": "stay-tribeca-1", "count": 0, "average_rank": None, "top": []},
            ]},
            reply_to=_ID(14),
        ),
        "route_reply": _envelope(
            12, "route_reply", "routing", ["stay"], "Route: 21 min walking, 12 transit, 9 driving",
            {"minutes": {"walking": 21, "transit": 12, "driving": 9},
             "origin": {"lat": 40.7465, "lng": -74.0014}, "destination": {"address": "368 9th Ave, New York, NY 10001", "lat": 40.7502, "lng": -73.9987}},
            reply_to=_ID(11),
        ),
    }


def valid_examples() -> dict[str, dict[str, Any]]:
    """Canonical JSON of one valid message per type (plus the Concierge's plan-for-review proposal)."""
    return {name: json.loads(serialize(parse_message(json.dumps(raw)))) for name, raw in _raw_examples().items()}


def _mutated(base: dict[str, Any], mutate) -> dict[str, Any]:
    clone = copy.deepcopy(base)
    mutate(clone)
    return clone


def invalid_examples() -> dict[str, tuple[dict[str, Any] | str, ErrorCode]]:
    """Messages every receiver must reject, with the error code it must answer."""
    valid = valid_examples()
    request, slot = valid["request"], valid["workout_slot"]
    return {
        "not_json": ("hello there", ErrorCode.INVALID_JSON),
        "code_fence": ("```json\n" + json.dumps(request) + "\n```", ErrorCode.INVALID_JSON),
        "missing_required_field": (_mutated(request, lambda m: m["payload"].pop("city")), ErrorCode.VALIDATION_FAILED),
        "wrong_type": (_mutated(request, lambda m: m.update(state_version="two")), ErrorCode.VALIDATION_FAILED),
        "bad_clock_time": (_mutated(slot, lambda m: m["payload"].update(start="5pm")), ErrorCode.VALIDATION_FAILED),
        "no_recipient": (_mutated(slot, lambda m: m.update(to=[])), ErrorCode.VALIDATION_FAILED),
        "unknown_major_version": (_mutated(request, lambda m: m.update(schema_version="2.0")), ErrorCode.UNSUPPORTED_VERSION),
        "unknown_type": (_mutated(request, lambda m: m.update(type="teleport")), ErrorCode.UNKNOWN_TYPE),
        "wrong_sender": (_mutated(request, lambda m: m.update({"from": "restaurant", "to": ["concierge"]})), ErrorCode.UNEXPECTED_SENDER),
    }
