import datetime as dt
import json
import logging
import threading
from zoneinfo import ZoneInfo

from plan_my_stay.enums import Mode
from plan_my_stay.models import LatLng
from plan_my_stay.routing.cache import RouteCache, RouteHit
from plan_my_stay.routing.maps import GeocodeResult

NY = ZoneInfo("America/New_York")
A = LatLng(lat=40.7066, lng=-74.0053)
B = LatLng(lat=40.7502, lng=-73.9987)
NOON = dt.datetime(2026, 11, 17, 12, 10, tzinfo=NY)
HIT = GeocodeResult(lat=40.7066, lng=-74.0053, formatted_address="199 Water St, New York, NY 10038, USA")


def test_geocode_cache_is_keyed_by_the_normalized_address():
    cache = RouteCache()
    assert cache.get_geocode("199 Water St, New York, NY 10038") is None
    cache.put_geocode("199 Water St, New York, NY 10038", HIT)
    assert cache.get_geocode("  199 WATER ST New York NY 10038 ") == HIT
    assert cache.get_geocode("200 Water St, New York, NY 10038") is None


def test_route_cache_hits_within_the_same_hour_only():
    cache = RouteCache()
    cache.put_route(A, B, Mode.WALKING, NOON, 61.2)
    assert cache.get_route(A, B, Mode.WALKING, NOON.replace(minute=59)) == RouteHit(61.2)
    assert cache.get_route(A, B, Mode.WALKING, NOON.replace(hour=13)) is None


def test_route_cache_key_includes_mode_and_direction():
    cache = RouteCache()
    cache.put_route(A, B, Mode.WALKING, NOON, 61.2)
    assert cache.get_route(A, B, Mode.TRANSIT, NOON) is None
    assert cache.get_route(B, A, Mode.WALKING, NOON) is None


def test_route_cache_same_instant_in_another_offset_is_a_hit():
    cache = RouteCache()
    cache.put_route(A, B, Mode.DRIVING, NOON, 19.3)
    assert cache.get_route(A, B, Mode.DRIVING, NOON.astimezone(dt.UTC)) == RouteHit(19.3)


def test_a_cached_no_route_is_a_hit_with_none_minutes():
    cache = RouteCache()
    cache.put_route(A, B, Mode.TRANSIT, NOON, None)
    hit = cache.get_route(A, B, Mode.TRANSIT, NOON)
    assert hit is not None and hit.minutes is None


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "cache" / "routes.json"
    first = RouteCache(path)
    first.put_geocode("199 Water St", HIT)
    first.put_route(A, B, Mode.WALKING, NOON, 61.2)
    first.put_route(A, B, Mode.TRANSIT, NOON, None)

    second = RouteCache(path)
    assert second.get_geocode("199 water st") == HIT
    assert second.get_route(A, B, Mode.WALKING, NOON) == RouteHit(61.2)
    assert second.get_route(A, B, Mode.TRANSIT, NOON) == RouteHit(None)
    assert json.loads(path.read_text())["version"] == 1


def test_a_corrupt_file_starts_empty_and_is_logged(tmp_path, caplog):
    path = tmp_path / "routes.json"
    path.write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="plan_my_stay.routing.cache"):
        cache = RouteCache(path)
    assert cache.get_geocode("anything") is None
    assert any("could not read" in r.getMessage() for r in caplog.records)
    cache.put_route(A, B, Mode.WALKING, NOON, 5.0)  # and it heals on the next write
    assert RouteCache(path).get_route(A, B, Mode.WALKING, NOON) == RouteHit(5.0)


def test_malformed_entries_are_skipped_not_loaded(tmp_path, caplog):
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({
        "version": 1,
        "geocodes": {"ok": {"lat": 1.0, "lng": 2.0, "formatted_address": "x"}, "bad": {"lat": "north"}},
        "routes": {"k1": 3.5, "k2": "soon", "k3": None},
    }))
    with caplog.at_level(logging.WARNING, logger="plan_my_stay.routing.cache"):
        cache = RouteCache(path)
    assert cache.get_geocode("ok") == GeocodeResult(lat=1.0, lng=2.0, formatted_address="x")
    assert cache.get_geocode("bad") is None
    assert any("skipped" in r.getMessage() for r in caplog.records)


def test_an_unwritable_path_does_not_break_the_cache(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = RouteCache(blocker / "routes.json")  # parent is a file, so every write fails
    with caplog.at_level(logging.WARNING, logger="plan_my_stay.routing.cache"):
        cache.put_route(A, B, Mode.WALKING, NOON, 5.0)
    assert cache.get_route(A, B, Mode.WALKING, NOON) == RouteHit(5.0)  # still served from memory
    assert any("could not save" in r.getMessage() for r in caplog.records)


def test_a_few_threads_can_write_at_once(tmp_path):
    path = tmp_path / "routes.json"
    cache = RouteCache(path)

    def work(worker: int) -> None:
        for i in range(25):
            point = LatLng(lat=40.0 + worker * 0.01, lng=-74.0 + i * 0.001)
            cache.put_route(point, B, Mode.WALKING, NOON, float(i))

    threads = [threading.Thread(target=work, args=(w,)) for w in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reloaded = RouteCache(path)
    for worker in range(6):
        for i in range(25):
            point = LatLng(lat=40.0 + worker * 0.01, lng=-74.0 + i * 0.001)
            assert reloaded.get_route(point, B, Mode.WALKING, NOON) == RouteHit(float(i))


def test_an_unknown_file_layout_starts_empty(tmp_path, caplog):
    for body in ["[]", json.dumps({"version": 2, "geocodes": {}, "routes": {}}), json.dumps({"version": 1, "geocodes": [], "routes": {}})]:
        path = tmp_path / "routes.json"
        path.write_text(body)
        with caplog.at_level(logging.WARNING, logger="plan_my_stay.routing.cache"):
            cache = RouteCache(path)
        assert cache.get_geocode("anything") is None
    assert sum("could not read" in r.getMessage() for r in caplog.records) == 3


def test_geocode_entries_of_the_wrong_shape_are_skipped(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({
        "version": 1,
        "geocodes": {"a": 5, "b": {"lat": "x", "lng": 1, "formatted_address": "y"}, "c": {"lat": 1, "lng": 2, "formatted_address": 7}},
        "routes": {"r": -1, "s": True},
    }))
    cache = RouteCache(path)
    assert [cache.get_geocode(k) for k in "abc"] == [None, None, None]


def test_a_failed_save_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    import os

    cache = RouteCache(tmp_path / "routes.json")

    def refuse(src, dst):
        raise PermissionError("no")

    monkeypatch.setattr(os, "replace", refuse)
    cache.put_route(A, B, Mode.WALKING, NOON, 5.0)
    assert [p.name for p in tmp_path.iterdir()] == []
