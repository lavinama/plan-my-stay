"""Small helpers shared by the memory tests: a controllable clock and a CogneeClient aimed at the fake server."""

import datetime as dt

from plan_my_stay.config import Secret
from plan_my_stay.memory.cognee_client import CogneeClient
from plan_my_stay.timeutil import TRIP_TZ

from .fake_cognee import API_KEY, BASE_URL, DATASET_ID, TENANT_ID


class FakeClock:
    """Returns a later instant on every call, so "newest wins" never depends on two writes sharing a timestamp.
    Tests move time with `advance` / `set` instead of sleeping."""

    def __init__(self) -> None:
        self._now = dt.datetime(2026, 11, 14, 16, 0, tzinfo=TRIP_TZ)

    def __call__(self) -> dt.datetime:
        value = self._now
        self._now += dt.timedelta(seconds=1)
        return value

    @property
    def now(self) -> dt.datetime:
        return self._now

    def set(self, when: dt.datetime) -> None:
        self._now = when

    def advance(self, **delta: float) -> None:
        self._now += dt.timedelta(**delta)


def make_client(**overrides: object) -> CogneeClient:
    options: dict[str, object] = {
        "base_url": BASE_URL,
        "api_key": Secret(API_KEY),
        "tenant_id": Secret(TENANT_ID),
        "dataset_id": DATASET_ID,
        "retry_delay_s": 0,
    }
    return CogneeClient(**{**options, **overrides})  # type: ignore[arg-type]
