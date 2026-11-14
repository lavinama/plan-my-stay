"""Start one seat on BAND (`scripts/run_seat.py <seat>`), refusing early and by name when it cannot work.

Checks run before anything connects, and report variable and seat names only, never values:
the seat's keys (`preflight`), then its entry in `agent_config.yaml`.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from plan_my_stay.config import MissingSecretError, Settings
from plan_my_stay.enums import SPECIALISTS, Seat
from plan_my_stay.preflight import check_seat
from plan_my_stay.seats import build_seat

_PLACEHOLDER = "<"  # agent_config.example.yaml's "<concierge api key>" style values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_seat.py", description="Run one Plan My Stay seat on BAND.")
    parser.add_argument("seat", choices=[s.value for s in Seat])
    parser.add_argument("--offline", action="store_true", help="fixtures and in-memory fakes instead of Google, Cognee and the LLM (no keys)")
    parser.add_argument("--stubs", action="store_true", help="run the stand-in for a specialist (stay, restaurant, gym) even if a live agent exists")
    parser.add_argument("--config", default=None, help="BAND agent_config.yaml (default: BAND_CONFIG_PATH or agent_config.yaml)")
    parser.add_argument("--seats-file", default="band_seats.yaml", help="seat -> BAND display name (not secret)")
    return parser


def _band_entry_problem(config: Path, seat: Seat) -> str | None:
    """Why this seat can't connect, or None. Names the seat and file, never a credential."""
    if not config.is_file():
        return f"{config} not found: copy agent_config.example.yaml to it and fill in the {seat.value} entry (it is git-ignored)"
    loaded: Any = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    entry = loaded.get(seat.value) if isinstance(loaded, Mapping) else None
    fields = [entry.get(k) for k in ("agent_id", "api_key")] if isinstance(entry, Mapping) else []
    if len(fields) != 2 or any(not isinstance(v, str) or not v.strip() or v.startswith(_PLACEHOLDER) for v in fields):
        return f"{config} has no usable {seat.value!r} entry (agent_id and api_key): register the seat at app.band.ai and paste both"
    return None


def run_seat_command(
    argv: Sequence[str],
    *,
    settings: Settings | None = None,
    runner: Callable[..., Any] | None = None,
) -> int:
    """Returns the exit code: 0 served and stopped, 1 keys missing, 2 BAND config or usage problem."""
    args = _parser().parse_args(argv)
    seat = Seat(args.seat)
    settings = settings if settings is not None else Settings.load()

    uses_keys = not args.offline and not (args.stubs and seat in SPECIALISTS)
    if uses_keys:
        readiness = check_seat(seat, settings)
        if not readiness.ready:
            print(f"{readiness}\nRun scripts/check_env.py to see every seat, or use --offline.", file=sys.stderr)
            return 1

    config = Path(args.config or settings.band_config_path)
    problem = _band_entry_problem(config, seat)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2

    try:
        handler = build_seat(seat, settings, offline=args.offline, use_stubs=args.stubs)
    except MissingSecretError as exc:
        print(exc, file=sys.stderr)
        return 1

    if runner is None:
        from plan_my_stay.band_adapter import run_seat  # noqa: PLC0415 - needs band-sdk; keep the import lazy

        runner = run_seat
    from plan_my_stay.band_adapter import SeatDirectory  # noqa: PLC0415

    directory = SeatDirectory.load(args.seats_file)
    try:
        asyncio.run(runner(seat.value, handler, config_path=config, directory=directory))
    except KeyboardInterrupt:
        pass
    return 0
