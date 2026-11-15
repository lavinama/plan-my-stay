"""The Stay agent's one integration point: handle(message) -> list[dict].

Built from Rohan's Stay Agent Spec v4 (placed at Rohan/stay-agent-spec.md).
D4: "Stay -> 6 stays -> Restaurant + Gym (cc Concierge) -> they send their
findings to the Concierge -> the Concierge picks the final 3 and sends them
to the user. The Stay agent does not pick, and doesn't re-rank on feedback."

This is the P0 stub from the spec's build order (0:00-0:15): "Stub handle()
returns 6 fixture stays. BAND round trip." No Keenable call yet -- Hop 1
(discovery) and Hop 2 (detail + SIE extract) plug in later behind
fixtures.discover_stays' signature without this file changing.

FR1: handle(message) -> list[dict], testable without BAND.
FR2: on `request`, run the pipeline; on `conflict` or a cheaper-options
     request, search again (cache first); anything else gets no reply.
FR4/D13: hard filters only -- budget (nights/guests are already fixed by
     the fixture data for this demo trip, so only budget is checked here).
FR8: rank by driving time to the meetings, then price, then rating. This is
     a driving-only P0 stand-in, not the full plan's "fastest of walking,
     transit, driving" -- see _driving_minutes() below.
FR10: send one stay_shortlist message with the ranked stays to the
     Concierge only. Per PR #10's current (still-draft) scope, the
     Concierge is the only seat Stay talks to; it queries Gym and
     Restaurant itself rather than having Stay broadcast to them directly.
"""

from __future__ import annotations

from typing import Any, Callable

from workers.stay import envelope, fixtures

STAY_HANDLED_TYPES = {"request", "conflict", "route_reply"}


def handle(
    message: dict[str, Any],
    *,
    discover: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    discover = discover or fixtures.discover_stays

    problems = envelope.validate_incoming(message)
    if problems:
        return [envelope.error_for(message, "invalid_envelope", problems)]

    if message["type"] == "error":
        return []

    if message["type"] not in envelope.KNOWN_TYPES:
        return [envelope.error_for(message, "unknown_type", ["type"])]

    if message["type"] not in STAY_HANDLED_TYPES:
        return []

    try:
        if message["type"] in ("request", "conflict"):
            return _handle_request(message, discover)
        return []  # route_reply: stub, P1+
    except KeyError as exc:
        return [envelope.error_for(message, "missing_field", [str(exc.args[0]) if exc.args else "unknown"])]
    except (TypeError, ValueError, LookupError) as exc:
        return [envelope.error_for(message, "handler_error", [str(exc)])]


# ---------------------------------------------------------------------------
# request / conflict (a conflict re-runs the same pipeline, cache-first per
# FR2; P0 has no cache yet, so it just re-discovers)


def _driving_minutes(stay: dict[str, Any]) -> float:
    """Average driving minutes to the meetings that count toward the stay
    (FR8's primary rank key). A stay missing a driving entry for some
    meeting is penalized, not silently treated as zero (fail-closed: an
    unknown commute is worse than a known one, never better).

    This is a driving-only P0 stand-in: the full plan ranks by the fastest
    of walking/transit/driving per meeting (once Routing seat times
    arrive), not driving alone. Meeting A (before check-in) is correctly
    left out of this average -- not because this function filters it, but
    because the fixture's `driving` field only ever has entries for
    meetings B and C (the `counts_for_stay=true` ones); there is no "A"
    key to average in. If a future fixture ever adds one, it would need to
    be explicitly excluded here.
    """
    driving = stay.get("driving") or {}
    if not driving:
        return float("inf")
    minutes = [leg.get("minutes") for leg in driving.values() if leg.get("minutes") is not None]
    if not minutes:
        return float("inf")
    return sum(minutes) / len(minutes)


def _augment_stay(stay: dict[str, Any], rank: int) -> dict[str, Any]:
    """Add the fields PR #10's Concierge contract requires on top of the
    raw fixture stay, without dropping anything already there -- the
    Concierge's parser ignores fields it doesn't recognize, so existing
    data (name, lat/lng, source, etc.) passes through untouched via the
    dict spread below.

    - rank: 1-indexed position in the caller's already-sorted order.
    - deciding_factor: always "commute" -- this P0 only ranks by commute,
      no other factor is implemented yet.
    - zone_id: derived from neighbourhood (lowercased, spaces -> hyphens),
      since the fixture data has no separate zone_id field.
    - cost_basis: "card_total_fees_unknown" for every stay -- none of the
      fixture stays confirm what fees `total_price` does or doesn't
      include, and guessing would be worse than flagging it unknown.
    - commute_minutes: the stay's `driving` field reshaped from
      {meeting_id: {"minutes": ...}} to {meeting_id: {"driving": ...}},
      so the key names the mode (driving) now that the contract expects
      room for other modes (walking, transit) alongside it later.
    - average_commute_minutes: reuses _driving_minutes() rather than
      recomputing the average a second way.
    """
    neighbourhood = stay.get("neighbourhood", "")
    zone_id = neighbourhood.lower().replace(" ", "-")
    driving = stay.get("driving") or {}
    commute_minutes = {
        meeting_id: {"driving": leg.get("minutes")} for meeting_id, leg in driving.items()
    }
    return {
        **stay,
        "rank": rank,
        "deciding_factor": "commute",
        "zone_id": zone_id,
        "cost_basis": "card_total_fees_unknown",
        "commute_minutes": commute_minutes,
        "average_commute_minutes": _driving_minutes(stay),
    }


def _passes_budget(stay: dict[str, Any], budget: dict[str, Any] | None) -> bool:
    if not budget or budget.get("amount") is None:
        return True
    price = stay.get("total_price")
    if price is None:
        return True  # unknown price isn't a known failure; don't drop on absence
    return price <= budget["amount"]


def _handle_request(message: dict[str, Any], discover) -> list[dict[str, Any]]:
    trip_id = message["trip_id"]
    user_id = message["user_id"]
    state_version = message["state_version"]
    payload = message["payload"]
    budget = payload.get("budget")

    candidates = discover(payload)
    within_budget = [s for s in candidates if _passes_budget(s, budget)]

    # FR8: driving time to the meetings first, then price, then rating
    # (higher rating first, so negate it for an ascending sort).
    ranked = sorted(
        within_budget,
        key=lambda s: (_driving_minutes(s), s.get("price_per_night") or float("inf"), -(s.get("rating") or 0)),
    )
    top = [_augment_stay(s, rank) for rank, s in enumerate(ranked[:6], start=1)]

    note = None
    if len(top) < 6:
        note = f"only {len(top)} stay(s) within budget after filtering; sending what there is"

    summary = f"{len(top)} stay(s) ranked by drive time, price, rating"
    if note:
        summary += f" ({note})"

    reply = envelope.build_message(
        type="stay_shortlist",
        from_="stay",
        # Concierge only (not Restaurant/Gym): per PR #10's current scope
        # the Concierge is the sole seat Stay replies to, and it queries
        # Gym/Restaurant itself.
        to=["concierge"],
        trip_id=trip_id,
        user_id=user_id,
        state_version=state_version,
        reply_to=message["message_id"],
        summary=summary,
        payload={"stays": top, **({"note": note} if note else {})},
    )
    return [reply]
