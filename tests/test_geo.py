"""Straight-line distance and the walking-time stand-in that the rehearsal specialists share."""

import math

import pytest

from plan_my_stay.geo import (
    STREET_DETOUR_FACTOR,
    WALKING_SPEED_M_PER_MIN,
    haversine_m,
    walk_minutes,
    within_walk,
)

EARTH_RADIUS_M = 6_371_000.0
ONE_DEGREE_M = math.pi / 180 * EARTH_RADIUS_M  # about 111.19 km along any meridian or along the equator


def test_the_same_point_is_zero_metres_away():
    assert haversine_m(40.7488, -73.9971, 40.7488, -73.9971) == 0.0


def test_one_degree_of_latitude_is_about_111_km():
    assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(ONE_DEGREE_M, abs=1.0)
    assert haversine_m(40.0, -74.0, 41.0, -74.0) == pytest.approx(ONE_DEGREE_M, abs=1.0)


def test_one_degree_of_longitude_shrinks_with_the_cosine_of_the_latitude():
    assert haversine_m(0.0, 10.0, 0.0, 11.0) == pytest.approx(ONE_DEGREE_M, abs=1.0)
    at_new_york = haversine_m(40.7, -74.0, 40.7, -73.0)
    assert at_new_york == pytest.approx(ONE_DEGREE_M * math.cos(math.radians(40.7)), rel=1e-3)


def test_opposite_sides_of_the_earth_are_half_the_circumference_apart():
    assert haversine_m(0.0, 0.0, 0.0, 180.0) == pytest.approx(math.pi * EARTH_RADIUS_M, abs=1.0)
    assert haversine_m(90.0, 0.0, -90.0, 0.0) == pytest.approx(math.pi * EARTH_RADIUS_M, abs=1.0)


def test_the_distance_is_the_same_in_both_directions():
    forward = haversine_m(40.7470, -74.0000, 40.7085, -74.0092)
    backward = haversine_m(40.7085, -74.0092, 40.7470, -74.0000)
    assert forward == pytest.approx(backward)


def test_a_known_manhattan_distance():
    # Chelsea's zone centre to the Financial District's, by hand: 0.0385 degrees of latitude is 4,281 m north-south
    # and 0.0092 degrees of longitude at 40.73 N is 775 m east-west, so the line between them is about 4,351 m.
    assert haversine_m(40.7470, -74.0000, 40.7085, -74.0092) == pytest.approx(4_351, abs=5)


def test_walking_is_the_straight_line_times_the_detour_at_4_8_km_per_hour():
    assert WALKING_SPEED_M_PER_MIN == pytest.approx(80.0)  # 4.8 km/h
    assert STREET_DETOUR_FACTOR == pytest.approx(1.3)
    assert walk_minutes(0.0) == 0.0
    assert walk_minutes(800.0) == pytest.approx(13.0)  # 800 m * 1.3 / 80 m/min
    assert walk_minutes(1000.0) == pytest.approx(16.25)


def test_a_negative_distance_is_rejected():
    with pytest.raises(ValueError):
        walk_minutes(-1.0)


def test_ten_minutes_of_walking_reaches_about_615_metres_in_a_straight_line():
    assert within_walk(615.0, 10.0)
    assert not within_walk(616.0, 10.0)


def test_the_walk_limit_is_inclusive():
    straight_line_m = 800.0
    assert within_walk(straight_line_m, walk_minutes(straight_line_m))


def test_a_zero_or_negative_budget_of_minutes_reaches_only_the_spot_you_stand_on():
    assert within_walk(0.0, 0.0)
    assert not within_walk(1.0, 0.0)
    assert not within_walk(0.0, -1.0)
