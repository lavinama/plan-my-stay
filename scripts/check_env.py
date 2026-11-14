"""Print which seats can start with the current .env / environment (names only, never values).

    .venv/bin/python scripts/check_env.py            # all six seats
    .venv/bin/python scripts/check_env.py routing    # one seat; exit code 1 if it is not ready
"""

import sys

from plan_my_stay.config import Settings
from plan_my_stay.enums import Seat
from plan_my_stay.preflight import check_all, check_seat


def main(argv: list[str]) -> int:
    settings = Settings.load()
    if argv:
        try:
            results = tuple(check_seat(Seat(name), settings) for name in argv)
        except ValueError as exc:
            print(f"unknown seat: {exc}. Seats: {', '.join(s.value for s in Seat)}", file=sys.stderr)
            return 2
    else:
        results = check_all(settings)
    for result in results:
        print(result)
    return 0 if all(r.ready for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
