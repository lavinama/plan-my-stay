"""SeatDirectory: seat <-> BAND participant display name, loaded from band_seats.yaml."""

from pathlib import Path

import pytest
import yaml

from plan_my_stay.band_adapter.directory import SeatDirectory, SeatDirectoryError
from plan_my_stay.enums import Seat

from .conftest import NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_shipped_band_seats_yaml_has_all_six_seats_with_distinct_names():
    directory = SeatDirectory.load(REPO_ROOT / "band_seats.yaml")
    assert {seat for seat in Seat} == set(directory.names)
    names = list(directory.names.values())
    assert len(set(n.casefold() for n in names)) == 6


def test_shipped_names_avoid_the_words_band_dislikes():
    directory = SeatDirectory.load(REPO_ROOT / "band_seats.yaml")
    for name in directory.names.values():
        words = {w.casefold() for w in name.split()}
        assert not words & {"assistant", "bot"}, name


def test_name_of_and_seat_of_round_trip(directory):
    for seat, name in NAMES.items():
        assert directory.name_of(seat) == name
        assert directory.seat_of(name) is seat


def test_seat_of_is_tolerant_about_case_spacing_and_at_handles(directory):
    assert directory.seat_of("  plan my stay   ROUTING ") is Seat.ROUTING
    assert directory.seat_of("@Plan My Stay Routing") is Seat.ROUTING
    # BAND handles are slugs, optionally with an owner prefix
    assert directory.seat_of("@mario/plan-my-stay-routing") is Seat.ROUTING
    assert directory.seat_of("plan-my-stay-gym-scout") is Seat.GYM


@pytest.mark.parametrize("unknown", [None, "", "   ", "Mario", "Plan My Stay"])
def test_seat_of_returns_none_for_a_person_or_unknown_name(directory, unknown):
    assert directory.seat_of(unknown) is None


def test_names_mapping_cannot_be_mutated(directory):
    with pytest.raises(TypeError):
        directory.names[Seat.GYM] = "Other"  # type: ignore[index]


def test_constructor_accepts_seat_values_as_strings():
    directory = SeatDirectory({seat.value: name for seat, name in NAMES.items()})
    assert directory.name_of(Seat.GYM) == NAMES[Seat.GYM]


def test_missing_seat_is_rejected_and_named():
    partial = {seat: name for seat, name in NAMES.items() if seat is not Seat.VERIFIER}
    with pytest.raises(SeatDirectoryError, match="verifier"):
        SeatDirectory(partial)


def test_unknown_seat_key_is_rejected():
    with pytest.raises(SeatDirectoryError, match="chef"):
        SeatDirectory({**{s.value: n for s, n in NAMES.items()}, "chef": "Plan My Stay Chef"})


@pytest.mark.parametrize("bad", ["", "   ", None, 7])
def test_blank_or_non_text_name_is_rejected(bad):
    with pytest.raises(SeatDirectoryError, match="gym"):
        SeatDirectory({**NAMES, Seat.GYM: bad})  # type: ignore[arg-type]


def test_duplicate_names_are_rejected_even_if_only_case_differs():
    with pytest.raises(SeatDirectoryError, match="same name"):
        SeatDirectory({**NAMES, Seat.GYM: NAMES[Seat.STAY].upper()})


def test_generic_names_log_a_warning(caplog):
    with caplog.at_level("WARNING"):
        SeatDirectory({**NAMES, Seat.GYM: "Gym Bot"})
    assert any("Gym Bot" in r.getMessage() for r in caplog.records)


def test_load_reads_the_seats_mapping(tmp_path):
    path = tmp_path / "seats.yaml"
    path.write_text(yaml.safe_dump({"seats": {s.value: n for s, n in NAMES.items()}}), encoding="utf-8")
    assert SeatDirectory.load(path).name_of(Seat.CONCIERGE) == NAMES[Seat.CONCIERGE]


def test_load_missing_file_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="nope.yaml"):
        SeatDirectory.load(tmp_path / "nope.yaml")


@pytest.mark.parametrize("content", ["", "- a\n- b\n", "seats: [a, b]\n", "other: 1\n"])
def test_load_rejects_a_file_without_a_seats_mapping(tmp_path, content):
    path = tmp_path / "seats.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SeatDirectoryError, match="seats"):
        SeatDirectory.load(path)


def test_load_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "seats.yaml"
    path.write_text("seats: [unclosed\n", encoding="utf-8")
    with pytest.raises(SeatDirectoryError, match="not valid YAML"):
        SeatDirectory.load(path)
