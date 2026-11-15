"""build_seat: any seat from settings. Specialists fall back to their stub (loudly) until the live agent exists."""

import logging
import sys
import types

import pytest

from plan_my_stay.config import MissingSecretError, Settings
from plan_my_stay.enums import Seat
from plan_my_stay.rehearsal.stubs import GymStub, RestaurantStub, StayStub
from plan_my_stay.seats import build_seat

FULL_ENV = {
    "GOOGLE_MAPS_API_KEY": "g-secret",
    "KEENABLE_API_KEY": "k-secret",
    "SUPERLINKED_API_KEY": "s-secret",
    "COGNEE_API_KEY": "c-secret",
    "COGNEE_TENANT_ID": "t-secret",
    "COGNEE_BASE_URL": "https://cognee.example.test",
    "COGNEE_DATASET_ID": "0b7c1c1e-2f0f-4a43-9d57-3a1a0c9d6e11",
}


def test_the_verifier_takes_its_fee_reserve_from_the_settings():
    seat = build_seat(Seat.VERIFIER, Settings.from_env({"FEE_RESERVE_PCT": "0.2"}))
    assert type(seat).__name__ == "VerifierSeat"


def test_routing_offline_runs_on_the_fixture_maps_client_without_a_key():
    seat = build_seat(Seat.ROUTING, Settings.from_env({}), offline=True)
    assert type(seat).__name__ == "RoutingSeat"


def test_routing_live_needs_the_google_key_and_names_it_when_missing():
    with pytest.raises(MissingSecretError, match="GOOGLE_MAPS_API_KEY"):
        build_seat(Seat.ROUTING, Settings.from_env({}))
    assert type(build_seat(Seat.ROUTING, Settings.from_env(FULL_ENV))).__name__ == "RoutingSeat"


def test_the_concierge_offline_uses_in_memory_memory_and_no_llm():
    seat = build_seat(Seat.CONCIERGE, Settings.from_env({}), offline=True)
    assert type(seat).__name__ == "ConciergeSeat"
    assert hasattr(seat, "handle_text")


def test_the_concierge_live_needs_the_memory_settings():
    with pytest.raises(MissingSecretError, match="COGNEE_API_KEY"):
        build_seat(Seat.CONCIERGE, Settings.from_env({}))
    assert type(build_seat(Seat.CONCIERGE, Settings.from_env(FULL_ENV))).__name__ == "ConciergeSeat"


@pytest.mark.parametrize("seat, stub", [(Seat.STAY, StayStub), (Seat.RESTAURANT, RestaurantStub), (Seat.GYM, GymStub)])
def test_a_specialist_without_a_live_agent_runs_its_stub_and_says_so(seat, stub, caplog):
    with caplog.at_level(logging.WARNING):
        built = build_seat(seat, Settings.from_env({}), offline=True)

    assert isinstance(built, stub)
    assert any("STUB" in r.message and seat.value in r.message for r in caplog.records)


def test_a_live_specialist_agent_is_used_when_its_module_exists(monkeypatch):
    live = object()
    module = types.ModuleType("plan_my_stay.agents.gym")
    module.build = lambda settings: live
    monkeypatch.setitem(sys.modules, "plan_my_stay.agents.gym", module)

    assert build_seat(Seat.GYM, Settings.from_env({})) is live


def test_use_stubs_forces_the_stub_even_when_a_live_agent_exists(monkeypatch):
    module = types.ModuleType("plan_my_stay.agents.gym")
    module.build = lambda settings: object()
    monkeypatch.setitem(sys.modules, "plan_my_stay.agents.gym", module)

    assert isinstance(build_seat(Seat.GYM, Settings.from_env({}), use_stubs=True), GymStub)


def test_a_broken_live_agent_is_an_error_not_a_silent_fallback_to_the_stub(monkeypatch):
    def boom(settings):
        raise RuntimeError("the gym agent is broken")

    module = types.ModuleType("plan_my_stay.agents.gym")
    module.build = boom
    monkeypatch.setitem(sys.modules, "plan_my_stay.agents.gym", module)

    with pytest.raises(RuntimeError, match="broken"):
        build_seat(Seat.GYM, Settings.from_env({}))


def test_the_concierge_talks_only_to_the_stay_agent_by_default():
    from plan_my_stay.enums import MessageType
    from plan_my_stay.messages import Message

    seat = build_seat(Seat.CONCIERGE, Settings.from_env({}), offline=True)

    machine = [m for m in seat.handle_text("Mario", "plan the demo trip") if isinstance(m, Message)]
    assert [(m.type, m.to) for m in machine] == [(MessageType.REQUEST, (Seat.STAY,))]


def test_the_full_flag_builds_the_concierge_that_also_uses_routing_and_the_verifier():
    from plan_my_stay.enums import MessageType
    from plan_my_stay.messages import Message

    seat = build_seat(Seat.CONCIERGE, Settings.from_env({}), offline=True, full=True)

    machine = [m for m in seat.handle_text("Mario", "plan the demo trip") if isinstance(m, Message)]
    assert machine and all(m.type is MessageType.ROUTE_REQUEST and m.to == (Seat.ROUTING,) for m in machine)  # it geocodes first
