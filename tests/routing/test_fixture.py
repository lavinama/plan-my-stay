import pytest

from plan_my_stay import demo
from plan_my_stay.enums import Mode
from plan_my_stay.models import LatLng
from plan_my_stay.routing.fixture import FixtureMapsClient
from plan_my_stay.routing.geo import haversine_km
from plan_my_stay.routing.maps import MapsClient, MapsError

ALL = (Mode.WALKING, Mode.TRANSIT, Mode.DRIVING)
WHEN = demo.at(17, 12)


def anchor(anchor_id: str) -> LatLng:
    a = next(a for a in demo.demo_anchors() if a.id == anchor_id)
    return LatLng(lat=a.lat, lng=a.lng)


def test_it_is_a_maps_client():
    assert isinstance(FixtureMapsClient(), MapsClient)


@pytest.mark.parametrize("anchor_id", ["A", "B", "C"])
def test_the_demo_addresses_geocode_to_the_demo_anchors(anchor_id):
    result = FixtureMapsClient().geocode(demo.MEETING_ADDRESSES[anchor_id])
    expected = anchor(anchor_id)
    assert (result.lat, result.lng) == (expected.lat, expected.lng)
    assert result.formatted_address


def test_demo_address_matching_ignores_case_punctuation_and_the_city_part():
    client = FixtureMapsClient()
    full = client.geocode("199 Water St, New York, NY 10038")
    assert client.geocode("199 WATER ST., NEW YORK NY 10038") == full
    assert client.geocode("199 Water St") == full


def test_other_addresses_get_a_stable_location_inside_manhattan():
    first = FixtureMapsClient().geocode("1 Example Plaza, New York, NY 10001")
    again = FixtureMapsClient().geocode("1 example plaza new york ny 10001")
    other = FixtureMapsClient().geocode("2 Example Plaza, New York, NY 10001")
    assert (first.lat, first.lng) == (again.lat, again.lng)
    assert (first.lat, first.lng) != (other.lat, other.lng)


# Approximate shore longitudes from landmarks, independent of the fixture's own formula:
# (latitude, Hudson shore, East River / Harlem River shore).
_SHORES = [
    (40.71, -74.0165, -73.9985),
    (40.74, -74.0085, -73.9740),
    (40.76, -73.9960, -73.9570),
    (40.78, -73.9890, -73.9490),
    (40.80, -73.9730, -73.9350),
    (40.82, -73.9610, -73.9340),
]


def _island_at(lat: float) -> tuple[float, float]:
    for (lat0, w0, e0), (lat1, w1, e1) in zip(_SHORES, _SHORES[1:]):
        if lat0 <= lat <= lat1:
            t = (lat - lat0) / (lat1 - lat0)
            return w0 + t * (w1 - w0), e0 + t * (e1 - e0)
    raise AssertionError(f"{lat} is outside the latitudes where the island's width is known")


def test_pseudo_locations_stay_on_the_island_for_many_addresses():
    client = FixtureMapsClient()
    for n in range(2000):
        r = client.geocode(f"{n} Imaginary Ave, New York")
        west, east = _island_at(r.lat)
        assert west <= r.lng <= east, f"{r.lat}, {r.lng} is in the water"


def test_a_blank_address_fails_to_geocode():
    with pytest.raises(MapsError) as err:
        FixtureMapsClient().geocode("   ")
    assert err.value.code == "geocode_failed"


def test_minutes_are_deterministic_and_ignore_the_departure_time():
    client = FixtureMapsClient()
    one = client.route_minutes(anchor("A"), anchor("B"), WHEN, ALL)
    two = client.route_minutes(anchor("A"), anchor("B"), demo.at(18, 9), ALL)
    assert one == two
    assert list(one) == list(ALL)


def test_a_to_b_numbers_are_sensible_for_lower_to_midtown_manhattan():
    km = haversine_km(anchor("A"), anchor("B"))
    minutes = FixtureMapsClient().route_minutes(anchor("A"), anchor("B"), WHEN, ALL)
    assert minutes[Mode.WALKING] == pytest.approx(km / 4.8 * 60, abs=0.06)
    assert minutes[Mode.TRANSIT] == pytest.approx(km / 15 * 60 + 5, abs=0.06)
    assert minutes[Mode.DRIVING] == pytest.approx(km / 18 * 60 + 3, abs=0.06)
    assert 55 < minutes[Mode.WALKING] < 70  # about 4.9 km on foot
    assert minutes[Mode.DRIVING] < minutes[Mode.TRANSIT] < minutes[Mode.WALKING]
    assert all(round(m, 1) == m for m in minutes.values())


def test_a_short_hop_is_fastest_on_foot_once_waits_are_counted():
    near = LatLng(lat=40.7502, lng=-73.9987)
    next_door = LatLng(lat=40.7512, lng=-73.9987)  # about 110 m
    minutes = FixtureMapsClient().route_minutes(near, next_door, WHEN, ALL)
    assert minutes[Mode.WALKING] < minutes[Mode.DRIVING] < minutes[Mode.TRANSIT]


def test_only_the_requested_modes_come_back():
    minutes = FixtureMapsClient().route_minutes(anchor("A"), anchor("C"), WHEN, [Mode.DRIVING])
    assert list(minutes) == [Mode.DRIVING]
    assert FixtureMapsClient().route_minutes(anchor("A"), anchor("C"), WHEN, []) == {}


def test_the_same_place_is_zero_minutes():
    minutes = FixtureMapsClient().route_minutes(anchor("B"), anchor("B"), WHEN, ALL)
    assert minutes == {Mode.WALKING: 0.0, Mode.TRANSIT: 0.0, Mode.DRIVING: 0.0}

