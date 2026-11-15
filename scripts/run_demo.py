"""Run the New York demo trip in one process: no keys, no network, no BAND.

    .venv/bin/python scripts/run_demo.py              # the Concierge and the Stay agent: the top 3 stays, then run 2
    .venv/bin/python scripts/run_demo.py --no-run2    # skip the second run for the same user
    .venv/bin/python scripts/run_demo.py --full       # the full pipeline: six seats, a block, a revision and a clear
    .venv/bin/python scripts/run_demo.py --full --no-block   # ... where the Restaurant stub's first pick is good

By default the Concierge talks only to the Stay agent (a stand-in from src/plan_my_stay/rehearsal/stubs.py) and
answers with the top three stays. With --full it also uses Routing (fixture maps), the Verifier and the Gym and
Restaurant stand-ins. It prints the room as people would read it in Band Desktop and what the person receives.
"""

import argparse
import sys
import time
from collections import Counter

from plan_my_stay.enums import MessageType
from plan_my_stay.memory.in_memory import InMemoryMemory
from plan_my_stay.rehearsal.room import Room
from plan_my_stay.rehearsal.wiring import build_rehearsal_seats

PERSON = "Mario"


def _run(memory: InMemoryMemory, text: str, *, closes_early: bool, stay_only: bool) -> tuple[Room, float]:
    room = Room(build_rehearsal_seats(memory, first_pick_closes_early=closes_early, stay_only=stay_only))
    started = time.perf_counter()
    room.say(PERSON, text)
    return room, time.perf_counter() - started


def _summary(room: Room, seconds: float) -> str:
    counts = Counter(m.type.value for m in room.messages)
    verdicts = [m.payload.status.value for m in room.messages if m.type is MessageType.VERDICT]
    return (
        f"{len(room.messages)} messages in {seconds * 1000:.0f} ms; "
        f"verdicts: {' -> '.join(verdicts) or 'none'}; "
        + ", ".join(f"{n} {t}" for t, n in sorted(counts.items()))
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="run the full pipeline (Routing, Verifier, Gym, Restaurant) instead of only the Stay agent")
    parser.add_argument("--no-block", action="store_true", help="with --full, start with a restaurant that is open late enough")
    parser.add_argument("--no-run2", action="store_true", help="skip the second run for the same user")
    args = parser.parse_args(argv)

    memory = InMemoryMemory()
    stay_only = not args.full
    room, seconds = _run(memory, "Plan my demo trip", closes_early=not args.no_block, stay_only=stay_only)
    print("=== Run 1: the room ===")
    print(room.format_transcript())
    print("\n=== Run 1: what the person receives ===")
    print(room.replies[-1].text if room.replies else "(no reply)")
    print(f"\n{_summary(room, seconds)}")
    if not room.replies:
        return 1

    if not args.no_run2:
        room2, seconds2 = _run(memory, "Plan my demo trip again", closes_early=not args.no_block, stay_only=stay_only)
        first_request = next(m for m in room2.messages if m.type is MessageType.REQUEST)
        remembered = {k: v for k, v in (first_request.payload.profile.model_extra or {}).items()}
        print("\n=== Run 2: same user, starts from remembered preferences ===")
        print(f"preferences remembered from run 1 and sent in the request: {remembered or 'none'}")
        print(_summary(room2, seconds2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
