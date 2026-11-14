"""Memory is best effort: if Cognee is down, planning continues (SEAT_PROTOCOL section 5).

`SafeMemory` wraps a MemoryPort so no call can fail the run. Each failure is logged with the exception type only
(never a message, which might carry a key or text from the user).
"""

import logging

from pydantic import JsonValue

from plan_my_stay.enums import Seat
from plan_my_stay.models import Profile
from plan_my_stay.ports import MemoryPort
from plan_my_stay.state import TripState

logger = logging.getLogger(__name__)


class SafeMemory:
    def __init__(self, memory: MemoryPort) -> None:
        self._memory = memory

    def load_profile(self, user_id: str) -> Profile:
        try:
            return self._memory.load_profile(user_id)
        except Exception as exc:  # noqa: BLE001 - memory is best effort
            logger.warning("memory: could not load the profile (%s); planning without it", type(exc).__name__)
            return Profile()

    def save_preference(self, *, user_id: str, trip_id: str, key: str, value: JsonValue, evidence: str) -> bool:
        try:
            self._memory.save_preference(user_id=user_id, trip_id=trip_id, key=key, value=value, evidence=evidence)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory: could not save preference %r (%s)", key, type(exc).__name__)
            return False

    def save_trip_state(self, state: TripState) -> bool:
        try:
            self._memory.save_trip_state(state)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory: could not save the trip state (%s)", type(exc).__name__)
            return False

    def log_trace(self, *, trip_id: str, user_id: str, step: str, outcome: str) -> bool:
        try:
            self._memory.log_trace(trip_id=trip_id, user_id=user_id, agent=Seat.CONCIERGE, step=step, outcome=outcome)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory: could not log trace %r (%s)", step, type(exc).__name__)
            return False
