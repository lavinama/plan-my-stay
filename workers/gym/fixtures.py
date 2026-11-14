"""Default, zero-network adapters: discovery and page-read/classify.

handle()'s `discover` and `fetch_and_classify` keyword arguments default to
the functions in this module, so the worker runs end-to-end on fixture data
with no network calls. docs/PLAN.md's P1 plan is "Keenable fetch with a
prompt as a stand-in" until Rohan's shared page reader lands, then the real
Keenable search + SIE-read pipeline; both will plug in here with these same
two function signatures, so handle.py never has to change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "gyms"


def discover_gyms(zone: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    """Fixture-only gym discovery for one zone.

    Loads fixtures/gyms/{zone_id}.json if the zone has an id with a matching
    file (z1.json, z2.json, ...). Otherwise falls back to
    fixtures/gyms/near_stay.json, which is what a "near the stay" search
    with no specific zone id should use. Returns [] if neither file exists,
    never fabricated gyms.
    """
    zone_id = (zone or {}).get("id")

    path = _FIXTURES_DIR / f"{zone_id}.json" if zone_id else None
    if path is None or not path.exists():
        path = _FIXTURES_DIR / "near_stay.json"

    if not path.exists():
        return []

    with open(path) as f:
        data = json.load(f)
    return data.get("gyms", [])


def fetch_and_classify(candidate: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """No-op passthrough for P0: fixture gym JSON already has every field
    (name, address, lat, lng, gym_type, hours, source) a real page read
    would fill in.

    This is the exact swap-in point for P1's real pipeline: Keenable
    `fetch_page_content` on the candidate's page, then a small SIE model
    (Qwen/Qwen3.5-4B per docs/PLAN.md) filling the same schema. Keep this
    signature (candidate, profile) -> candidate when replacing it.
    """
    return dict(candidate)
