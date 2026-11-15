"""Fixtures: a controllable clock, a respx-served fake Cognee, and the stores the contract suite runs against."""

from collections.abc import Iterator

import pytest
import respx

from plan_my_stay.memory.cognee_client import CogneeClient
from plan_my_stay.memory.cognee_memory import CogneeMemory
from plan_my_stay.ports import MemoryPort

from .backends import Backend, CogneeBackend, InMemoryBackend
from .fake_cognee import BASE_URL, FakeCognee
from .helpers import FakeClock, make_client


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def cognee_server() -> Iterator[FakeCognee]:
    fake = FakeCognee()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.route().mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
def cognee_client(cognee_server: FakeCognee) -> Iterator[CogneeClient]:
    with make_client() as client:
        yield client


@pytest.fixture
def cognee_memory(cognee_client: CogneeClient, clock: FakeClock) -> CogneeMemory:
    return CogneeMemory(cognee_client, clock=clock)


@pytest.fixture(params=["in_memory", "cognee"])
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    """Every contract test runs against both implementations."""
    if request.param == "in_memory":
        yield InMemoryBackend()
        return
    fake = FakeCognee()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.route().mock(side_effect=fake.handle)
        cognee_backend = CogneeBackend(fake)
        yield cognee_backend
        cognee_backend.close()


@pytest.fixture
def memory(backend: Backend, clock: FakeClock) -> MemoryPort:
    return backend.new_memory(clock)
