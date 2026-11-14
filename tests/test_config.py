"""Secrets come from the environment, are validated at startup and never leak into logs."""

import pytest

from plan_my_stay.config import MissingSecretError, Settings, load_dotenv_file

COGNEE_UUID = "0b7c1c1e-2f0f-4a43-9d57-3a1a0c9d6e11"


def test_from_env_reads_secrets_and_applies_defaults():
    settings = Settings.from_env({"GOOGLE_MAPS_API_KEY": "maps-secret-123"})

    assert settings.require("google_maps_api_key") == "maps-secret-123"
    assert settings.superlinked_base_url == "https://api.superlinked.com"
    assert settings.fee_reserve_pct == 0.15
    assert settings.band_config_path == "agent_config.yaml"


def test_repr_never_contains_a_secret():
    settings = Settings.from_env({"GOOGLE_MAPS_API_KEY": "maps-secret-123", "COGNEE_API_KEY": "cog-secret-456"})

    assert "maps-secret-123" not in repr(settings) and "cog-secret-456" not in repr(settings)
    assert "maps-secret-123" not in str(settings)


def test_require_names_every_missing_variable_but_no_values():
    settings = Settings.from_env({"COGNEE_API_KEY": "cog-secret-456"})

    with pytest.raises(MissingSecretError) as err:
        settings.require("google_maps_api_key", "cognee_api_key", "keenable_api_key")

    assert "GOOGLE_MAPS_API_KEY" in str(err.value) and "KEENABLE_API_KEY" in str(err.value)
    assert "COGNEE_API_KEY" not in str(err.value)
    assert "cog-secret-456" not in str(err.value)


def test_blank_values_count_as_missing():
    settings = Settings.from_env({"GOOGLE_MAPS_API_KEY": "   "})
    with pytest.raises(MissingSecretError):
        settings.require("google_maps_api_key")


def test_cognee_dataset_must_be_a_uuid_not_a_name():
    assert Settings.from_env({"COGNEE_DATASET_ID": COGNEE_UUID}).cognee_dataset_id == COGNEE_UUID
    with pytest.raises(ValueError, match="UUID"):
        Settings.from_env({"COGNEE_DATASET_ID": "plan_my_stay"})


def test_fee_reserve_must_be_a_non_negative_fraction():
    assert Settings.from_env({"FEE_RESERVE_PCT": "0.2"}).fee_reserve_pct == 0.2
    with pytest.raises(ValueError):
        Settings.from_env({"FEE_RESERVE_PCT": "-1"})


def test_load_dotenv_file_parses_comments_quotes_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\n\nA=1\nB="two words"\nC=\'x\'  # trailing\nexport D=4\n')

    assert load_dotenv_file(env_file) == {"A": "1", "B": "two words", "C": "x", "D": "4"}


def test_load_dotenv_file_returns_empty_when_missing(tmp_path):
    assert load_dotenv_file(tmp_path / "nope.env") == {}
