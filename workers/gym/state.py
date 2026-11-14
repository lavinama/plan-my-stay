"""Per-trip_id in-memory state for the Gym agent.

This is the worker's own scratch memory between calls to handle() within
one process -- not the authoritative trip state, which travels in the BAND
messages themselves (docs/PLAN.md: "Trip state travels in BAND messages
(versioned JSON); Cognee keeps a copy"). It exists so that, for example, a
later stay_pick message can tell which dates are still waiting for a
location and which date (Tuesday) was already finalized and must never be
touched again.
"""

from __future__ import annotations

from typing import Any

_STATE: dict[str, dict[str, Any]] = {}


def get(trip_id: str) -> dict[str, Any]:
    """Return this trip's state dict, creating an empty one if absent.

    Shape:
      profile: dict            -- the user's gym preferences, merged over time
      pending: dict[str, Any]  -- date -> window info awaiting a stay_pick
      items: dict[str, Any]    -- item_id -> {"item": ..., "zone_id": ...,
                                  "date": ..., "slot_id": ..., "excluded": [...]}
                                  (metadata alongside the sent item, so a
                                  conflict can re-run discovery for the same
                                  zone/date excluding the disputed candidate)
      final_dates: set[str]    -- dates already finalized; never revisited
    """
    if trip_id not in _STATE:
        _STATE[trip_id] = {
            "profile": {},
            "pending": {},
            "items": {},
            "final_dates": set(),
        }
    return _STATE[trip_id]


def reset() -> None:
    """Clear all trip state. Used by tests for isolation between cases."""
    _STATE.clear()
