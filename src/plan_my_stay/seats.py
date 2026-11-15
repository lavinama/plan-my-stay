"""Build any seat from settings: the one place that knows how each seat is wired to its dependencies.

`offline=True` swaps every external dependency for its fixture or in-memory fake (no keys, no network).
Specialists (Stay, Restaurant, Gym) are built from `plan_my_stay.agents.<seat>.build(settings)` when that
module exists; otherwise their rehearsal stub runs and a warning says so.
"""

import importlib
import logging
from typing import Any

from plan_my_stay.config import Settings
from plan_my_stay.enums import Seat
from plan_my_stay.rehearsal.stubs import GymStub, RestaurantStub, StayStub

log = logging.getLogger(__name__)

MAX_MAPS_CALLS = 500  # per-process cap on billable Google calls: a bug must not run up the Maps bill
_MEMORY_SETTINGS = ("cognee_api_key", "cognee_tenant_id", "cognee_base_url", "cognee_dataset_id")
_STUBS: dict[Seat, type] = {Seat.STAY: StayStub, Seat.RESTAURANT: RestaurantStub, Seat.GYM: GymStub}


def build_seat(seat: Seat, settings: Settings, *, offline: bool = False, use_stubs: bool = False, full: bool = False) -> Any:
    """`full` is for the Concierge: talk to Routing, the Verifier and the other specialists too, not only the Stay agent."""
    if seat is Seat.VERIFIER:
        from plan_my_stay.verifier.seat import VerifierSeat

        return VerifierSeat(fee_reserve_pct=settings.fee_reserve_pct)
    if seat is Seat.ROUTING:
        return _routing(settings, offline)
    if seat is Seat.CONCIERGE:
        return _concierge(settings, offline, full)
    return _specialist(seat, settings, use_stubs=use_stubs)


def _routing(settings: Settings, offline: bool) -> Any:
    from plan_my_stay.routing.seat import RoutingSeat

    if offline:
        from plan_my_stay.routing.fixture import FixtureMapsClient

        return RoutingSeat(FixtureMapsClient())
    settings.require("google_maps_api_key")  # raises MissingSecretError, so the key below is set
    from plan_my_stay.routing.google import GoogleMapsClient

    return RoutingSeat(GoogleMapsClient(settings.google_maps_api_key, max_calls=MAX_MAPS_CALLS))


def _concierge(settings: Settings, offline: bool, full: bool) -> Any:
    from plan_my_stay.concierge.seat import ConciergeSeat

    if offline:
        from plan_my_stay.memory.in_memory import InMemoryMemory

        return ConciergeSeat(InMemoryMemory(), None, fee_reserve_pct=settings.fee_reserve_pct, stay_only=not full)

    settings.require(*_MEMORY_SETTINGS)
    from plan_my_stay.memory.cognee_client import CogneeClient
    from plan_my_stay.memory.cognee_memory import CogneeMemory

    client = CogneeClient(
        base_url=settings.cognee_base_url,
        api_key=settings.cognee_api_key,
        tenant_id=settings.cognee_tenant_id,
        dataset_id=settings.cognee_dataset_id,
    )
    return ConciergeSeat(
        CogneeMemory(client), _frontier_llm(settings), fee_reserve_pct=settings.fee_reserve_pct, stay_only=not full
    )


def _frontier_llm(settings: Settings) -> Any | None:
    """The Concierge's frontier model, or None (the template itinerary and the demo-trip intake still work)."""
    if settings.anthropic_api_key is None:
        log.warning("ANTHROPIC_API_KEY is not set: the Concierge uses its template itinerary and cannot parse free-text trips")
        return None
    try:
        from plan_my_stay.concierge.llm import AnthropicLLM
    except ImportError:
        log.warning("AnthropicLLM is not available yet: the Concierge runs without a frontier model")
        return None
    return AnthropicLLM(settings.anthropic_api_key)


def _specialist(seat: Seat, settings: Settings, *, use_stubs: bool) -> Any:
    module_name = f"plan_my_stay.agents.{seat.value}"
    if not use_stubs:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name not in {"plan_my_stay.agents", module_name}:
                raise  # the live agent exists but one of its own imports is missing: that is a bug to fix, not a stub case
        else:
            return module.build(settings)
    log.warning("no live %s agent (%s): running the STUB with fixture data", seat.value, module_name)
    return _STUBS[seat]()
