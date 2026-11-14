import httpx
import pytest
import respx

from plan_my_stay.config import Secret
from plan_my_stay.routing.google import GoogleMapsClient

from .google_support import KEY, NOW


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def client() -> GoogleMapsClient:
    return GoogleMapsClient(Secret(KEY), http=httpx.Client(), clock=lambda: NOW)
