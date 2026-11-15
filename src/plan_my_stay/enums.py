"""Closed vocabularies shared by every seat. Values are lower case, as the plan requires."""

from enum import StrEnum


class Seat(StrEnum):
    """The six BAND seats. `to` and `from` in every message use these names."""

    CONCIERGE = "concierge"
    STAY = "stay"
    RESTAURANT = "restaurant"
    GYM = "gym"
    VERIFIER = "verifier"
    ROUTING = "routing"


SPECIALISTS: frozenset[Seat] = frozenset({Seat.STAY, Seat.RESTAURANT, Seat.GYM})


class MessageType(StrEnum):
    REQUEST = "request"
    PROPOSAL = "proposal"
    ZONE_FIT = "zone_fit"
    STAY_SHORTLIST = "stay_shortlist"
    STAY_PICK = "stay_pick"
    WORKOUT_SLOT = "workout_slot"
    PREFERENCE_LEARNED = "preference_learned"
    CONFLICT = "conflict"
    VERDICT = "verdict"
    ERROR = "error"
    ROUTE_REQUEST = "route_request"
    ROUTE_REPLY = "route_reply"
    HOTEL_QUERY = "hotel_query"  # Concierge asks Gym and Restaurant about each candidate hotel
    GYM_FIT = "gym_fit"  # Gym answers: the nearest gym to each hotel
    RESTAURANT_FIT = "restaurant_fit"  # Restaurant answers: the average rank of restaurants within a walk


class Mode(StrEnum):
    """Travel modes the Routing seat answers for."""

    WALKING = "walking"
    TRANSIT = "transit"
    DRIVING = "driving"


class VerdictStatus(StrEnum):
    CLEAR = "clear"
    WARN = "warn"  # something the Verifier could not check, such as an unknown opening time
    BLOCK = "block"


class LocationPrecision(StrEnum):
    EXACT = "exact"
    ZONE = "zone"  # only the neighborhood is known (Airbnb map-box cards)
