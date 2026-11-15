"""Default, zero-network stay discovery adapter.

Per Rohan's Stay Agent Spec v4 build order, P0 is a stub that returns 6
fixture stays with no live Keenable call -- "Stub handle() returns 6
fixture stays. BAND round trip." The real Hop 1 (Keenable discovery) + Hop
2 (Keenable detail + SIE extract) + routing-service driving times plug in
later behind this same function signature.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SIX_STAYS_PATH = Path(__file__).parent / "fixtures" / "stays" / "six_stays.json"


def discover_stays(request_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Fixture-only discovery: the 6 demo stays, unfiltered.

    `request_payload` is accepted (and ignored, for now) so the real Hop
    1/Hop 2 pipeline -- which needs the check-in/check-out dates, budget
    and meeting anchors with counts_for_stay=true -- can plug in here with
    the exact same signature later.
    """
    if not _SIX_STAYS_PATH.exists():
        return []
    with open(_SIX_STAYS_PATH) as f:
        data = json.load(f)
    return data.get("stays", [])
