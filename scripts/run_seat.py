"""Run one seat on BAND, with one command.

    .venv/bin/python scripts/run_seat.py concierge
    .venv/bin/python scripts/run_seat.py routing --offline      # fixture maps, no Google key
    .venv/bin/python scripts/run_seat.py gym --stubs            # the stand-in, until the live agent exists

It checks the seat's keys and its entry in agent_config.yaml first and says what is missing by name.
See README.md ("Secrets and who holds them") and docs/SEAT_PROTOCOL.md.
"""

import sys

from plan_my_stay.cli import run_seat_command

if __name__ == "__main__":
    sys.exit(run_seat_command(sys.argv[1:]))
