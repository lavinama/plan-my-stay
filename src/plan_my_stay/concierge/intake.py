"""Intake: a person's request becomes a `TripIntake` (city, dates, budget, guests, meetings, workouts).

An LLM reads the text with structured output, and pydantic validates what it returns. If the text contains
the word "demo", or the LLM is missing, fails or answers something invalid, the result is the New York demo
trip (docs/PLAN.md); `fell_back` tells the Concierge so, so the person is told.

Meeting addresses live only here, inside the Concierge. Agents get anchors (coordinates and time windows).
"""

import datetime as dt
import json
import logging
import re
import string
from typing import Any

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from plan_my_stay import demo
from plan_my_stay.concierge.llm import strict_schema
from plan_my_stay.concierge.slots import build_slots
from plan_my_stay.models import Budget, ClockTime, Frozen, Identifier, Slot
from plan_my_stay.ports import LLMPort
from plan_my_stay.timeutil import TRIP_TZ, now

logger = logging.getLogger(__name__)

_MAX_MEETINGS = 12
_MAX_TEXT_CHARS = 4000
_DEMO_WORD = re.compile(r"\bdemo\b", re.IGNORECASE)
_SKIPS_WORKOUTS = re.compile(
    r"\b(?:no|skip|without|don'?t want)\b[^.\n]{0,25}\b(?:workouts?|gym|exercise)\b", re.IGNORECASE
)

INTAKE_SYSTEM = (
    "You read a business traveler's trip request and return the trip as JSON. Use only what the text states; "
    "never invent a meeting, an address or a number. Dates are YYYY-MM-DD and times are 24-hour HH:MM in the "
    "meetings' local time. The budget is one total for the stay plus transport. Set wants_workouts to false only "
    "if the text says to skip the gym or workouts. Give each meeting's full street address as written."
)

# Hand-written, because structured output takes a strict subset of JSON Schema (no length or numeric limits).
INTAKE_SCHEMA: dict[str, Any] = strict_schema(
    {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City and region, e.g. 'New York, NY'"},
            "check_in": {"type": "string", "description": "YYYY-MM-DD"},
            "check_out": {"type": "string", "description": "YYYY-MM-DD"},
            "budget_amount": {"type": "number", "description": "Total for the stay plus transport"},
            "currency": {"type": "string", "description": "ISO 4217 code, e.g. USD"},
            "guests": {"type": "integer"},
            "wants_workouts": {"type": "boolean"},
            "meetings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "address": {"type": "string"},
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "start": {"type": "string", "description": "HH:MM, 24-hour"},
                        "end": {"type": "string", "description": "HH:MM, 24-hour"},
                    },
                    "required": ["address", "date", "start", "end"],
                },
            },
        },
        "required": [
            "city",
            "check_in",
            "check_out",
            "budget_amount",
            "currency",
            "guests",
            "wants_workouts",
            "meetings",
        ],
    }
)


class MeetingIntake(Frozen):
    """One meeting as the person gave it. `id` ("A", "B", ...) is what agents see in place of the address."""

    id: Identifier
    address: str = Field(min_length=3, repr=False)
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _ends_after_start(self) -> "MeetingIntake":
        if self.end <= self.start:
            raise ValueError("a meeting must end after it starts")
        return self


class TripIntake(Frozen):
    city: str = Field(min_length=1)
    check_in: dt.date
    check_out: dt.date
    budget: Budget
    guests: int = Field(default=1, ge=1)
    meetings: tuple[MeetingIntake, ...] = Field(min_length=1, repr=False)  # addresses never reach a log line
    wants_workouts: bool = True
    slots: tuple[Slot, ...] = ()  # the meal and workout windows for the stay
    is_demo: bool = False

    @model_validator(mode="after")
    def _check_out_after_check_in(self) -> "TripIntake":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class _RawMeeting(Frozen):
    address: str = Field(min_length=3)
    date: dt.date
    start: ClockTime
    end: ClockTime


class _RawIntake(Frozen):
    """What the LLM returns, before it becomes a TripIntake."""

    city: str = Field(min_length=1)
    check_in: dt.date
    check_out: dt.date
    budget_amount: float = Field(gt=0)
    currency: str = "USD"
    guests: int = Field(default=1, ge=1)
    wants_workouts: bool = True
    meetings: list[_RawMeeting] = Field(min_length=1, max_length=_MAX_MEETINGS)


def demo_intake(*, wants_workouts: bool = True) -> TripIntake:
    meetings = tuple(
        MeetingIntake(id=a.id, address=demo.MEETING_ADDRESSES[a.id], start=a.start, end=a.end)
        for a in demo.demo_anchors()
    )
    return TripIntake(
        city=demo.CITY,
        check_in=dt.date(2026, 11, 17),
        check_out=dt.date(2026, 11, 19),
        budget=Budget(currency="USD", amount=1000),
        meetings=meetings,
        wants_workouts=wants_workouts,
        slots=tuple(s for s in demo.demo_slots() if wants_workouts or s.kind != "workout"),
        is_demo=True,
    )


def parse_trip_request(text: str, llm: LLMPort | None, *, today: dt.date | None = None) -> TripIntake:
    """Read a trip request. Never raises: anything that cannot be read becomes the demo trip (see `fell_back`)."""
    wants_workouts = _SKIPS_WORKOUTS.search(text) is None
    if _DEMO_WORD.search(text):
        return demo_intake(wants_workouts=wants_workouts)
    if llm is None:
        logger.warning("no LLM to read the trip request; using the demo trip")
        return demo_intake(wants_workouts=wants_workouts)
    try:
        return _read_with_llm(text[:_MAX_TEXT_CHARS], llm, today or now().date())
    except Exception as exc:  # noqa: BLE001 - an LLM or validation failure must never end the run
        logger.warning("could not read the trip request (%s); using the demo trip", type(exc).__name__)
        return demo_intake(wants_workouts=wants_workouts)


def fell_back(text: str, intake: TripIntake) -> bool:
    """True when `intake` is the demo trip standing in for a request that could not be read (the text never said "demo")."""
    return intake.is_demo and _DEMO_WORD.search(text) is None


def _read_with_llm(text: str, llm: LLMPort, today: dt.date) -> TripIntake:
    prompt = f"Today is {today.isoformat()} ({today:%A}).\n\nTrip request:\n{text}"
    answer = llm.complete(system=INTAKE_SYSTEM, prompt=prompt, json_schema=INTAKE_SCHEMA)
    try:
        raw = _RawIntake.model_validate(json.loads(answer))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"the LLM's answer is not a valid trip: {type(exc).__name__}") from exc
    return _to_intake(raw)


_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}


def _currency(text: str) -> str:
    cleaned = text.strip()
    return _CURRENCY_SYMBOLS.get(cleaned, cleaned.upper())


def _to_intake(raw: _RawIntake) -> TripIntake:
    timed = sorted(
        (
            (
                dt.datetime.fromisoformat(f"{m.date.isoformat()}T{m.start}").replace(tzinfo=TRIP_TZ),
                dt.datetime.fromisoformat(f"{m.date.isoformat()}T{m.end}").replace(tzinfo=TRIP_TZ),
                m.address.strip(),
            )
            for m in raw.meetings
        ),
        key=lambda meeting: meeting[0],
    )
    meetings = tuple(
        MeetingIntake(id=string.ascii_uppercase[index], address=address, start=start, end=end)
        for index, (start, end, address) in enumerate(timed)
    )
    return TripIntake(
        city=raw.city.strip(),
        check_in=raw.check_in,
        check_out=raw.check_out,
        budget=Budget(currency=_currency(raw.currency), amount=raw.budget_amount),
        guests=raw.guests,
        meetings=meetings,
        wants_workouts=raw.wants_workouts,
        slots=build_slots(raw.check_in, raw.check_out, wants_workouts=raw.wants_workouts),
    )
