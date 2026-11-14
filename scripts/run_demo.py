"""Run the New York demo trip through the whole swarm in one process: no keys, no network, no BAND.

    .venv/bin/python scripts/run_demo.py              # the demo: block, revise, clear, then run 2
    .venv/bin/python scripts/run_demo.py --no-block   # the Restaurant stub's first pick is good
    .venv/bin/python scripts/run_demo.py --no-run2    # skip the second run for the same user

Real seats: Concierge, Routing (fixture maps), Verifier, memory (in-memory). Stand-ins: Stay,
Restaurant, Gym (src/plan_my_stay/rehearsal/stubs.py). It prints the room as people would read it
in Band Desktop, the itinerary, and what the Verifier did.
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


def _run(memory: InMemoryMemory, text: str, *, closes_early: bool) -> tuple[Room, float]:
    room = Room(build_rehearsal_seats(memory, first_pick_closes_early=closes_early))
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
    parser.add_argument("--no-block", action="store_true", help="start with a restaurant that is open late enough")
    parser.add_argument("--no-run2", action="store_true", help="skip the second run for the same user")
    args = parser.parse_args(argv)

    memory = InMemoryMemory()
    room, seconds = _run(memory, "Plan my demo trip", closes_early=not args.no_block)
    print("=== Run 1: the room ===")
    print(room.format_transcript())
    print("\n=== Run 1: the itinerary the person receives ===")
    print(room.replies[-1].text if room.replies else "(no reply)")
    print(f"\n{_summary(room, seconds)}")
    if not room.replies:
        return 1

    if not args.no_run2:
        room2, seconds2 = _run(memory, "Plan my demo trip again", closes_early=not args.no_block)
        first_request = next(m for m in room2.messages if m.type is MessageType.REQUEST)
        remembered = {k: v for k, v in (first_request.payload.profile.model_extra or {}).items()}
        print("\n=== Run 2: same user, starts from remembered preferences ===")
        print(f"preferences remembered from run 1 and sent in the request: {remembered or 'none'}")
        print(_summary(room2, seconds2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
