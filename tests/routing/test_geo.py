import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from plan_my_stay.models import LatLng
from plan_my_stay.routing.geo import coord_key, haversine_km, hour_key, normalize_address, street_part

NY = ZoneInfo("America/New_York")


def test_normalize_address_ignores_case_punctuation_and_spacing():
    assert normalize_address("  199 Water St.,  New York, NY 10038 ") == "199 water st new york ny 10038"
    assert normalize_address("199 WATER ST, New York,NY 10038") == "199 water st new york ny 10038"


def test_street_part_is_the_text_before_the_first_comma():
    assert street_part("368 9th Ave, New York, NY 10001") == "368 9th ave"
    assert street_part("368 9th Ave") == "368 9th ave"


def test_coord_key_rounds_to_about_a_metre():
    assert coord_key(LatLng(lat=40.7066, lng=-74.0053)) == "40.70660,-74.00530"
    assert coord_key(LatLng(lat=40.7066001, lng=-74.0053001)) == coord_key(LatLng(lat=40.7066, lng=-74.0053))


def test_hour_key_is_the_same_within_an_hour_whatever_the_offset():
    a = dt.datetime(2026, 11, 17, 16, 5, tzinfo=NY)  # 21:05Z
    b = dt.datetime(2026, 11, 17, 21, 59, tzinfo=dt.UTC)
    c = dt.datetime(2026, 11, 17, 17, 0, tzinfo=NY)  # 22:00Z
    assert hour_key(a) == hour_key(b) == "2026-11-17T21Z"
    assert hour_key(c) == "2026-11-17T22Z"


def test_haversine_known_distances():
    a, b = LatLng(lat=40.7066, lng=-74.0053), LatLng(lat=40.7502, lng=-73.9987)
    assert 4.7 < haversine_km(a, b) < 5.0
    assert haversine_km(a, a) == 0
    assert haversine_km(a, b) == haversine_km(b, a)


def test_hour_key_rejects_a_naive_datetime():
    with pytest.raises(ValueError):
        hour_key(dt.datetime(2026, 11, 17, 12, 0))
