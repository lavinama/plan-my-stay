"""The live MapsClient: Google's Geocoding API and Routes API `computeRouteMatrix`.

Shapes checked against Google's docs on 14 Nov 2026 (developers.google.com/maps/documentation/routes
and .../geocoding); whatever the docs do not settle is marked `# UNVERIFIED:` and listed in the
hand-off report for the first live call.

The key is the one secret here. It goes out in the `key` query parameter (Geocoding) and in the
`X-Goog-Api-Key` header (Routes). It is never put in an exception message, never logged, and
httpx's own request log line (which prints the Geocoding URL) is scrubbed by `_KeyRedactingFilter`.
"""

import logging
import re
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

import httpx

from plan_my_stay.config import Secret
from plan_my_stay.enums import Mode
from plan_my_stay.models import LatLng
from plan_my_stay.routing.maps import GeocodeResult, MapsError
from plan_my_stay.timeutil import now

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
# `status` must be in the mask: without it every element looks OK (Google's docs say so).
FIELD_MASK = "originIndex,destinationIndex,duration,status,condition"

_TRAVEL_MODE = {Mode.WALKING: "WALK", Mode.TRANSIT: "TRANSIT", Mode.DRIVING: "DRIVE"}
_GEOCODE_STATUS_CODE = {
    "ZERO_RESULTS": "geocode_failed",
    # UNVERIFIED: Google also uses OVER_DAILY_LIMIT for a bad key or billing switched off; we report it as quota.
    "OVER_QUERY_LIMIT": "quota_exceeded",
    "OVER_DAILY_LIMIT": "quota_exceeded",
    # REQUEST_DENIED (key restricted, API not enabled), INVALID_REQUEST, UNKNOWN_ERROR: upstream_error.
}
_GRPC_RESOURCE_EXHAUSTED = 8  # a matrix element's `status.code` when the quota ran out
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")  # "123s", up to nine fractional digits
_DEPARTURE_LEAD = timedelta(seconds=60)  # a DRIVE departure must not be in the past by the time Google reads it
_LOG_TEXT_LIMIT = 200


class _KeyRedactingFilter(logging.Filter):
    """Blanks `key=<value>` in any record from the `httpx` logger, which logs every request URL at INFO."""

    _KEY_PARAM = re.compile(r"([?&]key=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):  # a malformed record is not ours to fix; the handler will report it
            return True
        redacted = self._KEY_PARAM.sub(r"\1***", message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


def _install_log_redaction() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _KeyRedactingFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_KeyRedactingFilter())


def _coordinate(value: Any, limit: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not -limit <= value <= limit:
        raise MapsError("upstream_error", "Google sent an unusable coordinate")
    return float(value)


def _parse_duration_minutes(value: Any) -> float:
    match = _DURATION.match(value) if isinstance(value, str) else None
    if match is None:
        raise MapsError("upstream_error", "Google sent an unusable duration")
    return round(float(match.group(1)) / 60, 1)


class GoogleMapsClient:
    def __init__(
        self,
        api_key: Secret,
        http: httpx.Client | None = None,
        max_calls: int | None = None,
        *,
        timeout_s: float = 10.0,
        clock: Callable[[], datetime] = now,
    ) -> None:
        """`max_calls` caps Google requests over this client's life (every geocode and every per-mode matrix counts)."""
        if not isinstance(api_key, Secret):
            raise TypeError("api_key must be a config.Secret, so it can never be printed by accident")
        if not api_key.reveal().strip():
            raise ValueError("api_key must not be empty")
        if max_calls is not None and max_calls < 0:
            raise ValueError("max_calls must be zero or more")
        self._api_key = api_key
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=timeout_s)
        self._max_calls = max_calls
        self._clock = clock
        self._calls = 0
        self._lock = threading.Lock()
        _install_log_redaction()

    @property
    def calls_made(self) -> int:
        """Google requests sent (or attempted) so far."""
        with self._lock:
            return self._calls

    def close(self) -> None:
        """Close the HTTP client, but only one this object created."""
        if self._owns_http:
            self._http.close()

    # --- public interface (MapsClient) ---

    def geocode(self, address: str) -> GeocodeResult:
        text = address.strip()
        if not text:
            raise MapsError("geocode_failed", "empty address")
        response = self._send("geocoding", lambda: self._http.get(GEOCODE_URL, params={"address": text, "key": self._api_key.reveal()}))
        data = self._json(response, "geocoding")
        if not isinstance(data, dict):
            raise MapsError("upstream_error", "geocoding answered with something other than an object")
        status = data.get("status")
        if status != "OK":
            logger.warning("Google geocoding answered %s: %s", status, self._scrub(str(data.get("error_message", ""))))
            raise MapsError(_GEOCODE_STATUS_CODE.get(status, "upstream_error"), f"geocoding status {status}")
        return self._geocode_result(data, text)

    def route_minutes(
        self, origin: LatLng, destination: LatLng, depart_at: datetime, modes: Sequence[Mode]
    ) -> dict[Mode, float | None]:
        if depart_at.tzinfo is None or depart_at.utcoffset() is None:
            raise ValueError("depart_at must carry an offset")
        return {mode: self._matrix_minutes(origin, destination, depart_at, mode) for mode in dict.fromkeys(modes)}

    # --- geocoding ---

    @staticmethod
    def _geocode_result(data: dict[str, Any], address: str) -> GeocodeResult:
        results = data.get("results")
        first = results[0] if isinstance(results, list) and results else None
        try:
            location = first["geometry"]["location"]
            lat, lng = _coordinate(location["lat"], 90), _coordinate(location["lng"], 180)
        except (KeyError, TypeError):
            raise MapsError("upstream_error", "geocoding result has no usable location") from None
        formatted = first.get("formatted_address")
        return GeocodeResult(lat=lat, lng=lng, formatted_address=formatted if isinstance(formatted, str) and formatted else address)

    # --- routes ---

    def _matrix_minutes(self, origin: LatLng, destination: LatLng, depart_at: datetime, mode: Mode) -> float | None:
        body = self._matrix_body(origin, destination, depart_at, mode)
        headers = {"X-Goog-Api-Key": self._api_key.reveal(), "X-Goog-FieldMask": FIELD_MASK}
        response = self._send(f"route matrix ({mode.value})", lambda: self._http.post(MATRIX_URL, json=body, headers=headers))
        return self._matrix_element_minutes(self._json(response, "route matrix"))

    def _matrix_body(self, origin: LatLng, destination: LatLng, depart_at: datetime, mode: Mode) -> dict[str, Any]:
        body: dict[str, Any] = {
            "origins": [_waypoint(origin)],
            "destinations": [_waypoint(destination)],
            "travelMode": _TRAVEL_MODE[mode],
        }
        if mode is Mode.DRIVING:
            body["routingPreference"] = "TRAFFIC_AWARE"  # Google fails the request if this is sent for any other mode
        departure = self._departure_time(mode, depart_at)
        if departure is not None:
            body["departureTime"] = departure
        return body

    def _departure_time(self, mode: Mode, depart_at: datetime) -> str | None:
        """RFC 3339 with the offset the caller gave (the New York offset for trip times), or None to let Google use "now".

        Walking does not depend on the hour, so it never gets one (and Google documents no rule for it).
        A time in the past is allowed only for transit, so for driving it is left out and Google uses the request time.
        """
        if mode is Mode.WALKING:
            return None
        if mode is Mode.DRIVING and depart_at <= self._clock() + _DEPARTURE_LEAD:
            return None
        # UNVERIFIED: Google's docs show Zulu timestamps; an offset such as -05:00 should parse (proto3 JSON allows it).
        # UNVERIFIED: transit accepts 7 days back to 100 days ahead; a date outside that comes back as upstream_error.
        # UNVERIFIED: how far ahead a DRIVE departureTime may be.
        return depart_at.replace(microsecond=0).isoformat()

    @staticmethod
    def _matrix_element_minutes(data: Any) -> float | None:
        if not isinstance(data, list):
            raise MapsError("upstream_error", "route matrix answered with something other than a list")
        # UNVERIFIED: proto3 JSON may omit zero-valued fields, so a missing index means 0 (we only ever ask for 1 x 1).
        element = next(
            (e for e in data if isinstance(e, dict) and e.get("originIndex", 0) == 0 and e.get("destinationIndex", 0) == 0),
            None,
        )
        if element is None:
            raise MapsError("upstream_error", "route matrix had no element for the pair")
        condition = element.get("condition")
        if condition == "ROUTE_NOT_FOUND":
            return None
        status = element.get("status")
        status_code = status.get("code", 0) if isinstance(status, dict) else 0
        if status_code:
            raise MapsError("quota_exceeded" if status_code == _GRPC_RESOURCE_EXHAUSTED else "upstream_error", f"route matrix element status {status_code}")
        if condition != "ROUTE_EXISTS":
            raise MapsError("upstream_error", "route matrix element has an unknown condition")
        return _parse_duration_minutes(element.get("duration"))

    # --- plumbing ---

    def _reserve_call(self) -> None:
        with self._lock:
            if self._max_calls is not None and self._calls >= self._max_calls:
                raise MapsError("quota_exceeded", f"call cap of {self._max_calls} reached")
            self._calls += 1

    def _send(self, what: str, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Count the call, send it, and turn every failure into a MapsError that says nothing about the request."""
        self._reserve_call()
        try:
            response = send()
        except httpx.HTTPError as exc:
            # Not chained: an httpx error's text can hold the request URL, and the Geocoding URL holds the key.
            logger.warning("Google %s request failed: %s", what, type(exc).__name__)
            raise MapsError("upstream_error", f"{what} request failed ({type(exc).__name__})") from None
        if response.status_code == 429:
            logger.warning("Google %s answered HTTP 429", what)
            raise MapsError("quota_exceeded", f"{what} was rate limited")
        if response.status_code != 200:
            logger.warning("Google %s answered HTTP %d: %s", what, response.status_code, self._scrub(response.text[:_LOG_TEXT_LIMIT]))
            raise MapsError("upstream_error", f"{what} answered HTTP {response.status_code}")
        return response

    @staticmethod
    def _json(response: httpx.Response, what: str) -> Any:
        try:
            return response.json()
        except ValueError:
            raise MapsError("upstream_error", f"{what} answered with invalid JSON") from None

    def _scrub(self, text: str) -> str:
        """For log lines only: the key, if Google echoed it back, becomes ***."""
        return text.replace(self._api_key.reveal(), "***")


def _waypoint(point: LatLng) -> dict[str, Any]:
    return {"waypoint": {"location": {"latLng": {"latitude": point.lat, "longitude": point.lng}}}}
