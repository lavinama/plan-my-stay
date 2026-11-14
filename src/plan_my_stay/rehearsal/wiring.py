"""The rehearsal cast: real Concierge, Routing, Verifier and memory, with stand-in specialists.

Shared by the end-to-end test and `scripts/run_demo.py`, so what CI proves is what the demo runs.
"""

from collections.abc import Mapping
from typing import Any

from plan_my_stay.concierge.seat import ConciergeSeat
from plan_my_stay.enums import Seat
from plan_my_stay.memory.in_memory import InMemoryMemory
from plan_my_stay.ports import MemoryPort
from plan_my_stay.rehearsal.stubs import GymStub, RestaurantStub, StayStub
from plan_my_stay.routing.fixture import FixtureMapsClient
from plan_my_stay.routing.seat import RoutingSeat
from plan_my_stay.verifier.seat import VerifierSeat


def build_rehearsal_seats(
    memory: MemoryPort | None = None,
    *,
    first_pick_closes_early: bool = True,
    fee_reserve_pct: float = 0.15,
    overrides: Mapping[Seat, Any] | None = None,
) -> dict[Seat, Any]:
    """All six seats, offline. `first_pick_closes_early` makes the Restaurant stub's first pick close before dinner
    ends, so the Verifier's block fires every time (the demo's blockable-verdict moment).

    `overrides` swaps in a real agent for any seat, e.g. `overrides={Seat.GYM: my_gym_seat}`: that is how Rohan,
    Jwalin and Prathik drop their agent into the same end-to-end rehearsal in place of its stub."""
    shared_memory = memory if memory is not None else InMemoryMemory()
    cast = {
        Seat.CONCIERGE: ConciergeSeat(shared_memory, None, fee_reserve_pct=fee_reserve_pct),
        Seat.ROUTING: RoutingSeat(FixtureMapsClient()),
        Seat.VERIFIER: VerifierSeat(fee_reserve_pct=fee_reserve_pct),
        Seat.STAY: StayStub(),
        Seat.RESTAURANT: RestaurantStub(first_pick_closes_early=first_pick_closes_early),
        Seat.GYM: GymStub(),
    }
    return {**cast, **(overrides or {})}
