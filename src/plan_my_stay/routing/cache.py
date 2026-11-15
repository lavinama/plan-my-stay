"""Caches for the Routing seat: geocodes by normalized address, routes by places + mode + departure hour.

Safe for one process with a few threads (one lock around every read and write). With a `path` the
cache is a JSON file, written atomically on every change, so a restart keeps what Google already
answered. A cache that cannot be read or written never breaks a request: it logs and carries on.
"""

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from plan_my_stay.enums import Mode
from plan_my_stay.models import LatLng
from plan_my_stay.routing.geo import coord_key, hour_key, normalize_address
from plan_my_stay.routing.maps import GeocodeResult

logger = logging.getLogger(__name__)

FILE_VERSION = 1


@dataclass(frozen=True)
class RouteHit:
    """A cached route answer. `minutes` is None when Google found no route in that mode (also worth remembering)."""

    minutes: float | None


def _route_key(origin: LatLng, destination: LatLng, mode: Mode, depart_at: datetime) -> str:
    return f"{coord_key(origin)}>{coord_key(destination)}|{mode.value}|{hour_key(depart_at)}"


def _geocode_from_json(raw: object) -> GeocodeResult:
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    lat, lng, formatted = raw["lat"], raw["lng"], raw["formatted_address"]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lng)) or not isinstance(formatted, str):
        raise ValueError("wrong types")
    return GeocodeResult(lat=float(lat), lng=float(lng), formatted_address=formatted)


def _minutes_from_json(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise ValueError("minutes must be a non-negative number or null")
    return float(raw)


class RouteCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._geocodes: dict[str, GeocodeResult] = {}
        self._routes: dict[str, float | None] = {}
        if self._path is not None:
            self._load()

    # --- geocodes ---

    def get_geocode(self, address: str) -> GeocodeResult | None:
        with self._lock:
            return self._geocodes.get(normalize_address(address))

    def put_geocode(self, address: str, result: GeocodeResult) -> None:
        with self._lock:
            self._geocodes[normalize_address(address)] = result
            self._save()

    # --- routes ---

    def get_route(self, origin: LatLng, destination: LatLng, mode: Mode, depart_at: datetime) -> RouteHit | None:
        key = _route_key(origin, destination, mode, depart_at)
        with self._lock:
            if key not in self._routes:
                return None
            return RouteHit(self._routes[key])

    def put_route(self, origin: LatLng, destination: LatLng, mode: Mode, depart_at: datetime, minutes: float | None) -> None:
        key = _route_key(origin, destination, mode, depart_at)
        with self._lock:
            self._routes[key] = minutes
            self._save()

    # --- persistence ---

    def _load(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
                raise ValueError("unknown file layout")
            geocodes, routes = data.get("geocodes", {}), data.get("routes", {})
            if not isinstance(geocodes, dict) or not isinstance(routes, dict):
                raise ValueError("unknown file layout")
        except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            logger.warning("Route cache: could not read %s (%s); starting empty", self._path.name, type(exc).__name__)
            return
        skipped = 0
        for key, raw in geocodes.items():
            try:
                self._geocodes[key] = _geocode_from_json(raw)
            except (KeyError, ValueError):
                skipped += 1
        for key, raw in routes.items():
            try:
                self._routes[key] = _minutes_from_json(raw)
            except ValueError:
                skipped += 1
        if skipped:
            logger.warning("Route cache: skipped %d malformed entries in %s", skipped, self._path.name)

    def _save(self) -> None:
        """Write the whole cache to a temp file and rename it over the real one, so a crash never leaves half a file."""
        if self._path is None:
            return
        document = {
            "version": FILE_VERSION,
            "geocodes": {key: asdict(value) for key, value in self._geocodes.items()},
            "routes": self._routes,
        }
        tmp_name: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp", delete=False
            ) as tmp:
                tmp_name = tmp.name
                json.dump(document, tmp)
            os.replace(tmp_name, self._path)
        except OSError as exc:
            logger.warning("Route cache: could not save %s (%s); keeping results in memory only", self._path.name, type(exc).__name__)
            if tmp_name is not None:
                Path(tmp_name).unlink(missing_ok=True)
