"""Static zones (SEAT_PROTOCOL decision 3): the demo zones for New York, one padded box elsewhere."""

import pytest

from plan_my_stay import demo
from plan_my_stay.concierge.zones import StaticZones, ZoneProvider, widen_zones
from plan_my_stay.models import Anchor


def test_static_zones_implements_the_protocol():
    assert isinstance(StaticZones(), ZoneProvider)


@pytest.mark.parametrize("city", ["New York, NY", "new york", "New York City", "NEW YORK, NEW YORK"])
def test_new_york_gets_the_demo_zones(city):
    assert StaticZones().zones_for(city, demo.demo_anchors()) == demo.demo_zones()


def _anchor(id_: str, lat: float, lng: float) -> Anchor:
    return Anchor(id=id_, lat=lat, lng=lng, start=demo.at(17, 10), end=demo.at(17, 11), counts_for_stay=True)


def test_another_city_gets_one_zone_covering_the_anchors_plus_a_margin():
    anchors = (_anchor("A", 51.50, -0.12), _anchor("B", 51.53, -0.08))
    (zone,) = StaticZones().zones_for("London, UK", anchors)

    assert zone.id == "z1" and zone.name == "London"
    assert zone.bounds.sw.lat < 51.50 and zone.bounds.ne.lat > 51.53
    assert zone.bounds.sw.lng < -0.12 and zone.bounds.ne.lng > -0.08


def test_a_single_anchor_still_gets_a_box_with_area():
    (zone,) = StaticZones().zones_for("Boston, MA", (_anchor("A", 42.36, -71.06),))

    assert zone.bounds.ne.lat > zone.bounds.sw.lat
    assert zone.bounds.ne.lng > zone.bounds.sw.lng


def test_the_box_stays_inside_valid_coordinates():
    (zone,) = StaticZones().zones_for("Nowhere", (_anchor("A", 89.99, 179.99),))

    assert zone.bounds.ne.lat <= 90 and zone.bounds.ne.lng <= 180


def test_no_anchors_means_no_zones_rather_than_a_made_up_one():
    assert StaticZones().zones_for("Boston, MA", ()) == ()


def test_widen_zones_grows_every_box_and_keeps_ids_and_names():
    widened = widen_zones(demo.demo_zones())

    for before, after in zip(demo.demo_zones(), widened, strict=True):
        assert (after.id, after.name) == (before.id, before.name)
        assert after.bounds.ne.lat > before.bounds.ne.lat and after.bounds.sw.lat < before.bounds.sw.lat
        assert after.bounds.ne.lng > before.bounds.ne.lng and after.bounds.sw.lng < before.bounds.sw.lng
