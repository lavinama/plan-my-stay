"""Seat <-> BAND participant display name, loaded from `band_seats.yaml` (not secret).

BAND addresses people and agents by mention, and a mention is resolved from the participant's
handle, display name or id (`band.runtime.tools.agent.AgentTools._resolve_mentions`). Every
message names its recipients as seats (`to: ["stay"]`), so the adapter needs this table to turn
a seat into the display name it mentions, and to recognise which seat sent a message.
"""

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import yaml

from plan_my_stay.enums import Seat

logger = logging.getLogger(__name__)

DEFAULT_SEATS_PATH = Path("band_seats.yaml")
_DISLIKED_WORDS = frozenset({"assistant", "bot"})  # docs/PLAN.md: BAND dislikes names like "Assistant" or "Bot"


# UNVERIFIED: that the participant `name` BAND reports (a message's sender_name, a room participant's
# name) is exactly the display name typed at app.band.ai. The SDK source resolves mentions by handle,
# then name, then id; scripts/band_roundtrip.py compares the registered names with band_seats.yaml live.


class SeatDirectoryError(ValueError):
    """`band_seats.yaml` (or the mapping given to SeatDirectory) is not usable."""


def _key(name: str) -> str:
    """A comparison key that ignores case, spacing, a leading `@`, an `owner/` prefix and `-` versus space.

    BAND handles are slugs such as `@owner/plan-my-stay-routing`, while the display name is
    "Plan My Stay Routing"; both must resolve to the same seat.
    """
    text = name.strip().lstrip("@").rsplit("/", 1)[-1]
    return " ".join(re.split(r"[\s_-]+", text.casefold())).strip()


def _seat(raw: object) -> Seat:
    try:
        return Seat(raw)
    except ValueError:
        known = ", ".join(s.value for s in Seat)
        raise SeatDirectoryError(f"unknown seat {raw!r} in the seat directory (seats are: {known})") from None


class SeatDirectory:
    """Immutable map between the six seats and their BAND display names."""

    def __init__(self, names: Mapping[Seat | str, str]) -> None:
        by_seat: dict[Seat, str] = {}
        for raw_seat, raw_name in names.items():
            seat = _seat(raw_seat)
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise SeatDirectoryError(f"seat {seat.value!r} needs a non-empty display name")
            by_seat[seat] = " ".join(raw_name.split())

        missing = sorted(s.value for s in Seat if s not in by_seat)
        if missing:
            raise SeatDirectoryError(f"missing seats in the seat directory: {', '.join(missing)}")

        by_key: dict[str, Seat] = {}
        for seat, name in by_seat.items():
            key = _key(name)
            if key in by_key:
                raise SeatDirectoryError(
                    f"seats {by_key[key].value!r} and {seat.value!r} have the same name ({name!r}); BAND mentions need distinct names"
                )
            by_key[key] = seat
            if _DISLIKED_WORDS & set(key.split()):
                logger.warning("seat %r is named %r: BAND dislikes names with 'Assistant' or 'Bot'", seat.value, name)

        self._by_seat: Mapping[Seat, str] = MappingProxyType({seat: by_seat[seat] for seat in Seat})
        self._by_key: Mapping[str, Seat] = MappingProxyType(by_key)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_SEATS_PATH) -> "SeatDirectory":
        """Read `band_seats.yaml`: a `seats:` mapping of seat name to display name. Relative paths use the working directory."""
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(f"seat directory not found at {path}; it ships in the repo root as band_seats.yaml") from None
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SeatDirectoryError(f"{path} is not valid YAML: {exc}") from exc
        seats = data.get("seats") if isinstance(data, dict) else None
        if not isinstance(seats, dict):
            raise SeatDirectoryError(f"{path} must contain a `seats:` mapping of seat name to BAND display name")
        return cls(seats)

    @property
    def names(self) -> Mapping[Seat, str]:
        return self._by_seat

    def name_of(self, seat: Seat) -> str:
        """The display name BAND knows this seat by (what a mention uses)."""
        return self._by_seat[seat]

    def seat_of(self, name: str | None) -> Seat | None:
        """The seat behind a BAND display name or handle, or None for a person or anyone unknown."""
        if not name or not name.strip():
            return None
        return self._by_key.get(_key(name))

    def __repr__(self) -> str:
        return f"SeatDirectory({dict((s.value, n) for s, n in self._by_seat.items())!r})"
