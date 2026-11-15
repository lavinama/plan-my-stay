"""Preflight: which seats can start with the keys that are set. Names only, never values."""

import pytest

from plan_my_stay.config import Settings
from plan_my_stay.enums import Seat
from plan_my_stay.preflight import SEAT_REQUIREMENTS, check_all, check_seat

FULL_ENV = {
    "GOOGLE_MAPS_API_KEY": "g-secret",
    "KEENABLE_API_KEY": "k-secret",
    "SUPERLINKED_API_KEY": "s-secret",
    "COGNEE_API_KEY": "c-secret",
    "COGNEE_TENANT_ID": "t-secret",
    "COGNEE_BASE_URL": "https://cognee.example.test",
    "COGNEE_DATASET_ID": "0b7c1c1e-2f0f-4a43-9d57-3a1a0c9d6e11",
    "ANTHROPIC_API_KEY": "a-secret",
}


def test_every_seat_has_requirements():
    assert set(SEAT_REQUIREMENTS) == set(Seat)


def test_the_verifier_is_plain_code_and_needs_no_keys():
    assert check_seat(Seat.VERIFIER, Settings.from_env({})).ready


def test_routing_needs_only_the_google_key():
    assert not check_seat(Seat.ROUTING, Settings.from_env({})).ready
    assert check_seat(Seat.ROUTING, Settings.from_env({"GOOGLE_MAPS_API_KEY": "g-secret"})).ready


def test_the_concierge_needs_memory_but_only_prefers_the_frontier_model_key():
    env = {k: v for k, v in FULL_ENV.items() if k != "ANTHROPIC_API_KEY"}
    result = check_seat(Seat.CONCIERGE, Settings.from_env(env))

    assert result.ready  # the template itinerary works without the frontier model
    assert result.optional_missing == ("ANTHROPIC_API_KEY",)


def test_a_specialist_needs_keenable_superlinked_and_memory():
    result = check_seat(Seat.GYM, Settings.from_env({"KEENABLE_API_KEY": "k-secret"}))

    assert not result.ready
    assert {"SUPERLINKED_API_KEY", "COGNEE_API_KEY", "COGNEE_DATASET_ID"} <= set(result.missing)
    assert "KEENABLE_API_KEY" not in result.missing


def test_the_report_names_variables_but_never_values():
    results = check_all(Settings.from_env(FULL_ENV))

    assert all(r.ready for r in results)
    rendered = " ".join(str(r) for r in results)
    assert "secret" not in rendered


@pytest.mark.parametrize("seat", list(Seat))
def test_full_environment_makes_every_seat_ready(seat):
    assert check_seat(seat, Settings.from_env(FULL_ENV)).ready
