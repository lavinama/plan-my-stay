"""Shared constants and builders for the Google client tests."""

from plan_my_stay import demo
from plan_my_stay.enums import Mode
from plan_my_stay.models import LatLng

KEY = "AIzaSyTESTKEY_0123456789-abcdef"
A = LatLng(lat=40.7066, lng=-74.0053)
B = LatLng(lat=40.7502, lng=-73.9987)
DEPART = demo.at(17, 16)  # Tue 17 Nov 16:00 -05:00
NOW = demo.at(14, 12)  # three days before the trip
ALL = (Mode.WALKING, Mode.TRANSIT, Mode.DRIVING)

GEOCODE_OK = {
    "status": "OK",
    "results": [
        {
            "formatted_address": "199 Water St, New York, NY 10038, USA",
            "geometry": {"location": {"lat": 40.7066, "lng": -74.0053}, "location_type": "ROOFTOP"},
        }
    ],
}


def element(duration: str | None = "1260s", condition: str = "ROUTE_EXISTS", **extra) -> dict:
    body: dict = {"condition": condition, "status": {}, **extra}
    if duration is not None:
        body["duration"] = duration
        body["distanceMeters"] = 4900
    return body
