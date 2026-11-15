"""Which seats can start with the keys that are set. Reports variable names only, never values.

The accounts and keys are the one part of the build that no code can speed up (billing, sign-ups,
BAND seat registration), so this turns "everyone is blocked without them" into a checklist.
"""

from dataclasses import dataclass

from plan_my_stay.config import Settings
from plan_my_stay.enums import Seat

_MEMORY = ("cognee_api_key", "cognee_tenant_id", "cognee_base_url", "cognee_dataset_id")


@dataclass(frozen=True)
class Requirements:
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


# Settings field names; the environment variable is the upper-cased name.
SEAT_REQUIREMENTS: dict[Seat, Requirements] = {
    Seat.CONCIERGE: Requirements(required=_MEMORY, optional=("anthropic_api_key",)),
    Seat.ROUTING: Requirements(required=("google_maps_api_key",)),
    Seat.VERIFIER: Requirements(required=()),
    Seat.STAY: Requirements(required=("keenable_api_key", "superlinked_api_key", *_MEMORY)),
    Seat.RESTAURANT: Requirements(required=("keenable_api_key", "superlinked_api_key", *_MEMORY)),
    Seat.GYM: Requirements(required=("keenable_api_key", "superlinked_api_key", *_MEMORY)),
}


@dataclass(frozen=True)
class SeatReadiness:
    seat: Seat
    missing: tuple[str, ...]  # environment variable names
    optional_missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing

    def __str__(self) -> str:
        if self.missing:
            return f"{self.seat.value}: NOT READY, missing {', '.join(self.missing)}"
        note = f" (optional, not set: {', '.join(self.optional_missing)})" if self.optional_missing else ""
        return f"{self.seat.value}: ready{note}"


def check_seat(seat: Seat, settings: Settings) -> SeatReadiness:
    needs = SEAT_REQUIREMENTS[seat]

    def absent(names: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(n.upper() for n in names if getattr(settings, n) is None)

    return SeatReadiness(seat=seat, missing=absent(needs.required), optional_missing=absent(needs.optional))


def check_all(settings: Settings) -> tuple[SeatReadiness, ...]:
    return tuple(check_seat(seat, settings) for seat in Seat)
