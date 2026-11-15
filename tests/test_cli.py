"""`scripts/run_seat.py <seat>`: start one seat on BAND, refusing early (and by name) when it can't work."""

import pytest

from plan_my_stay.cli import run_seat_command
from plan_my_stay.config import Settings
from plan_my_stay.enums import Seat

SEATS_YAML = """seats:
  concierge: "Plan My Stay Concierge"
  stay: "Plan My Stay Stay Finder"
  restaurant: "Plan My Stay Restaurant Scout"
  gym: "Plan My Stay Gym Scout"
  verifier: "Plan My Stay Verifier"
  routing: "Plan My Stay Routing"
"""


class Recorder:
    """Stands in for the BAND connection: records what it was asked to serve instead of connecting."""

    def __init__(self):
        self.calls = []
        self.seats = []

    async def __call__(self, seat_name, seat, *, config_path, directory, **kwargs):
        self.seats.append(seat)
        self.calls.append((seat_name, type(seat).__name__, str(config_path), directory.name_of(Seat(seat_name))))


@pytest.fixture
def files(tmp_path):
    seats = tmp_path / "band_seats.yaml"
    seats.write_text(SEATS_YAML)
    config = tmp_path / "agent_config.yaml"
    # Entries for every seat the tests start; stay is deliberately absent (see the missing-entry test).
    config.write_text("".join(f"{seat}:\n  agent_id: id-{seat}\n  api_key: key-{seat}\n" for seat in ("verifier", "routing", "gym", "concierge")))
    return seats, config


def _run(argv, files, settings=None, recorder=None):
    seats, config = files
    recorder = recorder or Recorder()
    code = run_seat_command(
        [*argv, "--seats-file", str(seats), "--config", str(config)],
        settings=settings or Settings.from_env({}),
        runner=recorder,
    )
    return code, recorder


def test_the_verifier_needs_no_keys_and_is_served_under_its_display_name(files):
    code, recorder = _run(["verifier"], files)

    assert code == 0
    assert recorder.calls == [("verifier", "VerifierSeat", recorder.calls[0][2], "Plan My Stay Verifier")]


def test_a_seat_with_missing_keys_is_refused_naming_the_variables_not_values(files, capsys):
    code, recorder = _run(["routing"], files, settings=Settings.from_env({}))

    assert code == 1 and recorder.calls == []
    err = capsys.readouterr().err
    assert "GOOGLE_MAPS_API_KEY" in err and "routing" in err


def test_offline_skips_the_key_check_and_uses_fixtures(files):
    code, recorder = _run(["routing", "--offline"], files)

    assert code == 0 and recorder.calls[0][:2] == ("routing", "RoutingSeat")


def test_a_specialist_runs_its_stub_when_asked(files):
    code, recorder = _run(["gym", "--stubs"], files)

    assert code == 0 and recorder.calls[0][:2] == ("gym", "GymStub")


def test_a_missing_band_config_is_reported_without_connecting(tmp_path, capsys):
    seats = tmp_path / "band_seats.yaml"
    seats.write_text(SEATS_YAML)
    recorder = Recorder()

    code = run_seat_command(
        ["verifier", "--seats-file", str(seats), "--config", str(tmp_path / "nope.yaml")],
        settings=Settings.from_env({}), runner=recorder,
    )

    assert code == 2 and recorder.calls == []
    assert "agent_config.example.yaml" in capsys.readouterr().err


def test_a_config_without_this_seats_entry_is_reported(files, capsys):
    code, recorder = _run(["stay", "--stubs"], files)  # the config has no stay entry

    assert code == 2 and recorder.calls == []
    assert "stay" in capsys.readouterr().err


def test_an_unknown_seat_is_a_usage_error(files):
    with pytest.raises(SystemExit) as exc:
        _run(["intern"], files)
    assert exc.value.code == 2


def _first_machine_message(recorder):
    from plan_my_stay.messages import Message

    out = recorder.seats[0].handle_text("Mario", "plan the demo trip")
    return [m for m in out if isinstance(m, Message)][0]


def test_the_concierge_talks_only_to_the_stay_agent_unless_asked_for_the_full_pipeline(files):
    from plan_my_stay.enums import MessageType

    _, stay_only = _run(["concierge", "--offline"], files)
    _, full = _run(["concierge", "--offline", "--full"], files)

    assert _first_machine_message(stay_only).type is MessageType.REQUEST
    assert _first_machine_message(full).type is MessageType.ROUTE_REQUEST
