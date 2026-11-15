"""The Concierge seat: the orchestrator, and the only seat that talks to people (SEAT_PROTOCOL sections 1, 2, 4, 5).

`handle_text` starts a run (or takes feedback on a delivered one); `handle` drives the rest from the messages of
Routing, the specialists and the Verifier. Per-trip state lives in a frozen `Run` replaced on every step. The
steps themselves are in `pipeline.py`. The Concierge gates the budget and ranks the hotels from what the specialists return.
"""

import datetime as dt
import logging
import re
import threading
import uuid
from collections.abc import Callable, Sequence

from plan_my_stay import demo
from plan_my_stay.concierge.fmt import fmt_day, fmt_money
from plan_my_stay.concierge.intake import fell_back, parse_trip_request
from plan_my_stay.concierge.outbox import geocode_request
from plan_my_stay.concierge.pipeline import Pipeline, Result
from plan_my_stay.concierge.ranking import Weights
from plan_my_stay.concierge.run import Phase, Run
from plan_my_stay.concierge.safe_memory import SafeMemory
from plan_my_stay.concierge.zones import StaticZones, ZoneProvider
from plan_my_stay.enums import MessageType
from plan_my_stay.messages import Message
from plan_my_stay.models import Profile
from plan_my_stay.ports import LLMPort, MemoryPort
from plan_my_stay.seat import Outgoing, TextReply

logger = logging.getLogger(__name__)

_NEW_TRIP = re.compile(r"\b(?:new trip|another trip|plan (?:me )?a (?:new )?trip|start over)\b", re.IGNORECASE)
_REJECT = re.compile(
    r"\b(?:reject(?:ed)?|no thanks|don'?t like|dislike|hate|doesn'?t work|not (?:good|great|happy)|wrong|bad|change|instead"
    r"|too (?:far|expensive|pricey|noisy|loud|small|late|early))\b",
    re.IGNORECASE,
)
_ACCEPT = re.compile(
    r"\b(?:accept(?:ed)?|approve[ds]?|looks? good|love|great|perfect|book it|i'?ll take it|sounds good"
    r"|works for me|thank(?:s| you)|yes)\b",
    re.IGNORECASE,
)
_NEEDS_DETAILS = (
    "Tell me the city, your dates, your total budget and your meetings (address and time), and I will plan the trip. "
    'Say "demo" to try the New York demo trip.'
)


def classify_feedback(text: str) -> str:
    """`rejected`, `accepted`, or `commented` when the text is neither: a plain keyword check, no model call."""
    if _REJECT.search(text):
        return "rejected"
    return "accepted" if _ACCEPT.search(text) else "commented"


class ConciergeSeat:
    """Implements `TextSeat`. Dependencies come in through the constructor; nothing here touches the network itself."""

    def __init__(
        self,
        memory: MemoryPort,
        llm: LLMPort | None,
        *,
        fee_reserve_pct: float = 0.15,
        zones: ZoneProvider | None = None,
        check_in_time: str = "15:00",
        weights: Weights | None = None,
    ) -> None:
        if fee_reserve_pct < 0:
            raise ValueError("fee_reserve_pct must not be negative")
        self._memory = SafeMemory(memory)
        self._llm = llm
        self._pipeline = Pipeline(
            self._memory,
            llm,
            fee_reserve_pct=fee_reserve_pct,
            zones=zones or StaticZones(),
            check_in_time=dt.time.fromisoformat(check_in_time),
            weights=weights or Weights(),
        )
        self._runs: dict[str, Run] = {}  # by trip_id; each value is replaced, never mutated
        self._latest: dict[str, str] = {}  # person -> their latest trip_id
        self._lock = threading.RLock()
        self._handlers: dict[MessageType, Callable[[Run, Message], Result]] = {
            MessageType.ROUTE_REPLY: self._pipeline.on_route_reply,
            MessageType.STAY_SHORTLIST: self._pipeline.on_shortlist,
            MessageType.PROPOSAL: self._pipeline.on_proposal,
            MessageType.WORKOUT_SLOT: self._pipeline.on_workout_slot,
            MessageType.STAY_PICK: self._pipeline.on_stay_pick,
            MessageType.GYM_FIT: self._pipeline.on_gym_fit,
            MessageType.RESTAURANT_FIT: self._pipeline.on_restaurant_fit,
            MessageType.PREFERENCE_LEARNED: self._pipeline.on_preference,
            MessageType.VERDICT: self._pipeline.on_verdict,
            MessageType.ERROR: self._pipeline.on_error,
        }

    # --- prose from a person ---------------------------------------------------------------

    def handle_text(self, sender: str, text: str) -> Sequence[Outgoing]:
        with self._lock:
            body = text.strip()
            if not body:
                return [TextReply(text=_NEEDS_DETAILS, mentions=(sender,))]
            run = self._runs.get(self._latest.get(sender, ""))
            if run is not None and not _NEW_TRIP.search(body):
                if run.phase is Phase.DELIVERED:
                    return self._feedback(run, body)
                return [
                    TextReply(
                        text=f"I am still planning your trip to {run.intake.city}. I will send it once it is checked.",
                        mentions=(sender,),
                    )
                ]
            return self._start(sender, body)

    def _start(self, sender: str, text: str) -> list[Outgoing]:
        """SEAT_PROTOCOL section 5, steps 1 and 2: read the request, then ask Routing to locate each meeting."""
        intake = parse_trip_request(text, self._llm)
        trip_id = str(uuid.uuid4())
        profile = self._memory.load_profile(sender)
        if intake.is_demo and profile == Profile():
            profile = demo.demo_profile()  # a new user trying the demo gets the demo traveler's preferences
        lookups = [geocode_request(trip_id=trip_id, user_id=sender, meeting=meeting) for meeting in intake.meetings]
        run = Run(
            trip_id=trip_id,
            user_id=sender,
            person=sender,
            intake=intake,
            profile=profile,
            pending_geocodes={
                lookup.message_id: meeting.id for lookup, meeting in zip(lookups, intake.meetings, strict=True)
            },
        )
        self._runs[trip_id] = run
        self._latest[sender] = trip_id
        opening = (
            "I could not read that as a trip request, so I am planning the New York demo trip instead. "
            if fell_back(text, intake)
            else ""
        )
        count = len(intake.meetings)
        meetings = f"{count} meeting{'s' if count != 1 else ''}"
        planning = (
            f"{opening}Planning your trip to {intake.city}, {fmt_day(intake.check_in)} to {fmt_day(intake.check_out)}, "
            f"budget {fmt_money(intake.budget.amount, intake.budget.currency)}. First I am locating your {meetings}."
        )
        return [TextReply(text=planning, mentions=(sender,)), *lookups]

    def _feedback(self, run: Run, text: str) -> list[Outgoing]:
        """After delivery: an accept or reject goes to memory as plain text with its reason."""
        verdict = classify_feedback(text)
        city = run.intake.city
        self._memory.save_preference(
            user_id=run.user_id,
            trip_id=run.trip_id,
            key="trip_feedback",
            value=verdict,
            evidence=f"The user {verdict} the {city} plan: {text}",
        )
        self._memory.log_trace(
            trip_id=run.trip_id, user_id=run.user_id, step="user_feedback", outcome=f"{verdict}: {text}"
        )
        thanks = {
            "accepted": "Glad it works. I noted that you accepted this plan.",
            "rejected": "Thanks, I noted that this plan did not work and why.",
            "commented": "Thanks, I noted that.",
        }[verdict]
        return [TextReply(text=f'{thanks} Say "new trip" to plan another.', mentions=(run.person,))]

    # --- messages from the other seats -----------------------------------------------------

    def handle(self, message: Message) -> Sequence[Outgoing]:
        with self._lock:
            run = self._runs.get(message.trip_id)
            if run is None:
                logger.info("ignoring %s from %s: no run for that trip", message.type.value, message.sender.value)
                return []
            handler = self._handlers.get(message.type)
            if handler is None:
                logger.info(
                    "ignoring %s from %s: the Concierge does not act on it", message.type.value, message.sender.value
                )
                return []
            new_run, outgoing = handler(run, message)
            self._runs[run.trip_id] = new_run
            return outgoing
