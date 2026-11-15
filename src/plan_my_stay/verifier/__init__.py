"""The Verifier seat: plain-code checks that can block the plan (docs/SEAT_PROTOCOL.md section 4).

`is_open_during` is shared with the Restaurant and Gym agents (import it from `plan_my_stay.verifier.hours`
or from here).
"""

from plan_my_stay.verifier.checks import SEVERITY, Rule, check_plan, is_blocking, owner_of
from plan_my_stay.verifier.hours import is_open_during
from plan_my_stay.verifier.seat import VerifierSeat

__all__ = ["Rule", "SEVERITY", "VerifierSeat", "check_plan", "is_blocking", "is_open_during", "owner_of"]
