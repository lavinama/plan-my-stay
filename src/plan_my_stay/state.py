"""Trip state: versioned, immutable JSON that travels in the BAND messages.

The Concierge raises `state_version` on every change (`bump`). The Verifier checks the
exact plan it is given and answers with its `plan_hash`; the Concierge delivers only a
plan whose hash matches. Cognee keeps a copy for memory, not as the source of truth.
"""

import datetime as dt
import hashlib
import json
from typing import Any

from pydantic import Field

from plan_my_stay.enums import Seat
from plan_my_stay.fit import RankedStay
from plan_my_stay.models import (
    Anchor,
    Budget,
    Conflict,
    Frozen,
    Leg,
    PlanEntry,
    Profile,
    Slot,
    StayOption,
    Zone,
)

# What defines "the plan" for hashing. Version, round and open conflicts change as the
# plan is reviewed without changing the plan, so they are not part of the hash.
_PLAN_FIELDS = frozenset({"anchors", "budget", "stay", "entries", "legs"})


class TripState(Frozen):
    trip_id: str
    user_id: str
    state_version: int = Field(default=0, ge=0)
    city: str
    check_in: dt.date
    check_out: dt.date
    guests: int = Field(default=1, ge=1)
    anchors: tuple[Anchor, ...]
    zones: tuple[Zone, ...] = ()
    slots: tuple[Slot, ...] = ()
    budget: Budget
    profile: Profile = Profile()
    roster: tuple[Seat, ...] = ()
    round: int = Field(default=0, ge=0, le=2)  # revisions are capped at two; then the Concierge delivers with a warning
    stay: StayOption | None = None
    stay_shortlist: tuple[StayOption, ...] = ()
    # The Concierge's top hotels, best first. The alternatives are not checked day by day, so they are not in the plan hash.
    ranked_stays: tuple[RankedStay, ...] = ()
    entries: tuple[PlanEntry, ...] = ()
    legs: tuple[Leg, ...] = ()
    open_conflicts: tuple[Conflict, ...] = ()

    def bump(self, **changes: Any) -> "TripState":
        """Return a new state with `changes` applied and `state_version` raised by one. The original is untouched."""
        unknown = set(changes) - set(type(self).model_fields)
        if unknown:
            raise TypeError(f"unknown TripState field(s): {sorted(unknown)}")
        data = {name: getattr(self, name) for name in type(self).model_fields}
        data.update(changes)
        data["state_version"] = self.state_version + 1
        return type(self).model_validate(data)

    def plan_hash(self) -> str:
        """SHA-256 of the canonical JSON of the plan: what the Verifier checked and the Concierge delivers."""
        material = self.model_dump(mode="json", include=_PLAN_FIELDS)
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
