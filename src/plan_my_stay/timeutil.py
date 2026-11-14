"""The trip's one time zone. All timestamps carry an offset; clock times are 24-hour HH:MM."""

from datetime import datetime
from zoneinfo import ZoneInfo

TRIP_TZ = ZoneInfo("America/New_York")


def now() -> datetime:
    """Current time with the trip time zone's offset (never naive)."""
    return datetime.now(TRIP_TZ)
