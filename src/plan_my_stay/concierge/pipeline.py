"""The Concierge's run after the request is understood: collect, gate, route, verify, deliver (SEAT_PROTOCOL section 5).

Every step takes the current `Run` and one message and returns `(new Run, what to send)`. Nothing is mutated; the
seat stores the new Run. A message that arrives in the wrong phase is logged and ignored: the turn still ends
(the adapter sends `band_no_reply` for an empty result).
"""

import datetime as dt
import logging
from collections.abc import Sequence

from plan_my_stay.concierge.assemble import (
    build_anchors,
    build_state,
    conflict_owners,
    merge_entries,
)
from plan_my_stay.concierge.fmt import fmt_money
from plan_my_stay.concierge.gate import GateResult, gate_stay
from plan_my_stay.concierge.gating import (
    ask_cheaper,
    settle_close_call,
    skipped_notes,
    widen_and_ask,
)
from plan_my_stay.concierge.hotels import hotel_points
from plan_my_stay.concierge.itinerary import labels_for, render_itinerary
from plan_my_stay.concierge.legs import leg_gaps, locate_stay, plan_legs
from plan_my_stay.concierge.outbox import (
    hotel_query,
    leg_request,
    resend,
    specialist_request,
    verifier_proposal,
)
from plan_my_stay.concierge.ranking import TOP_N, Weights, keep_stay_order, rank_hotels
from plan_my_stay.concierge.run import (
    MAX_ROUNDS,
    PendingLeg,
    Phase,
    Result,
    Run,
    drop,
    put,
    reply,
)
from plan_my_stay.concierge.safe_memory import SafeMemory
from plan_my_stay.concierge.top_stays import render_top_stays
from plan_my_stay.concierge.transport import build_leg
from plan_my_stay.concierge.zones import ZoneProvider
from plan_my_stay.enums import Seat, VerdictStatus
from plan_my_stay.fit import RankedStay
from plan_my_stay.messages import Message
from plan_my_stay.messages.payloads import (
    ErrorPayload,
    GymFitPayload,
    PreferenceLearnedPayload,
    ProposalPayload,
    RestaurantFitPayload,
    RouteReplyPayload,
    StayPickPayload,
    StayShortlistPayload,
    VerdictPayload,
    WorkoutSlotPayload,
)
from plan_my_stay.models import Conflict, LatLng, Leg, StayOption
from plan_my_stay.ports import LLMPort
from plan_my_stay.state import TripState

logger = logging.getLogger(__name__)

_COLLECTING = (Phase.REQUESTED, Phase.COLLECTING)
_FIT_SEATS = (Seat.GYM, Seat.RESTAURANT)  # the specialists asked to measure from each candidate hotel
_MAX_RESENDS = 2  # a rejected request is sent again at most twice; then the seat is dropped from the run


class Pipeline:
    def __init__(
        self,
        memory: SafeMemory,
        llm: LLMPort | None,
        *,
        fee_reserve_pct: float,
        zones: ZoneProvider,
        check_in_time: dt.time,
        weights: Weights = Weights(),
        stay_only: bool = False,
    ) -> None:
        self._memory = memory
        self._llm = llm
        self._reserve = fee_reserve_pct
        self._zones = zones
        self._check_in_time = check_in_time
        self._weights = weights
        self._stay_only = stay_only  # the Stay agent is the only seat the Concierge talks to

    # --- small helpers ---------------------------------------------------------------------

    def finish(self, run: Run, text: str) -> Result:
        """End the run with a message to the person (a failure, or the reason there is no plan)."""
        return run.with_(phase=Phase.DELIVERED), [reply(run, text)]

    @staticmethod
    def ignore(run: Run, message: Message, why: str) -> Result:
        logger.info(
            "ignoring %s from %s in phase %s: %s", message.type.value, message.sender.value, run.phase.value, why
        )
        return run, []

    # --- Routing's answers -----------------------------------------------------------------

    def on_route_reply(self, run: Run, message: Message) -> Result:
        request_id = message.reply_to
        if request_id is not None and run.phase is Phase.GEOCODING and request_id in run.pending_geocodes:
            return self._geocoded(run, request_id, message.payload)
        if request_id is not None and run.phase is Phase.ROUTING and request_id in run.pending_legs:
            return self._leg_answered(run, request_id, message.payload)
        return self.ignore(run, message, "no lookup of ours is waiting for it")

    def _geocoded(self, run: Run, request_id: str, payload: RouteReplyPayload) -> Result:
        meeting_id = run.pending_geocodes[request_id]
        place = payload.origin or payload.destination
        if payload.error is not None or place is None or place.lat is None or place.lng is None:
            return self.finish(
                run,
                f"I could not locate meeting {meeting_id}'s address ({payload.error or 'no coordinates'}). "
                "Please check it and send the trip again.",
            )
        run = run.with_(
            pending_geocodes=drop(run.pending_geocodes, request_id),
            geocoded=put(run.geocoded, meeting_id, LatLng(lat=place.lat, lng=place.lng)),
        )
        return (run, []) if run.pending_geocodes else self._send_requests(run)

    def _send_requests(self, run: Run) -> Result:
        """Every meeting is located: build the trip state, save a copy and ask the roster."""
        anchors = build_anchors(run.intake, run.geocoded, self._check_in_time)
        state = build_state(
            trip_id=run.trip_id,
            user_id=run.user_id,
            intake=run.intake,
            anchors=anchors,
            profile=run.profile,
            zones=self._zones,
        )
        self._memory.save_trip_state(state)
        requests = [specialist_request(state, seat) for seat in state.roster]
        return run.with_(state=state, phase=Phase.REQUESTED, requests={m.to[0]: m for m in requests}), list(requests)

    # --- the specialists' answers ----------------------------------------------------------

    def on_shortlist(self, run: Run, message: Message) -> Result:
        payload: StayShortlistPayload = message.payload
        revising = run.phase is Phase.REVIEWING and Seat.STAY in run.awaiting
        if run.phase not in _COLLECTING and not revising:
            return self.ignore(run, message, "not collecting stays")
        run = run.with_(shortlist=payload, shortlist_id=message.message_id)
        if revising:
            return self._revised(run, Seat.STAY, message)
        return self._advance(run.with_(phase=Phase.COLLECTING))

    def on_proposal(self, run: Run, message: Message) -> Result:
        payload: ProposalPayload = message.payload
        agent = message.sender
        if payload.agent is not agent:
            return self.ignore(run, message, "the proposal's agent is not its sender")
        if agent not in run.roster:
            return self.ignore(run, message, "that seat is not in this run")
        revising = run.phase is Phase.REVIEWING and agent in run.awaiting
        if run.phase not in _COLLECTING and not revising:
            return self.ignore(run, message, "not collecting proposals")
        run = run.with_(proposals=put(run.proposals, agent, payload))
        if revising:
            return self._revised(run, agent, message)
        return self._advance(run.with_(phase=Phase.COLLECTING))

    def on_workout_slot(self, run: Run, message: Message) -> Result:
        payload: WorkoutSlotPayload = message.payload
        if run.phase in (Phase.GEOCODING, Phase.DELIVERED):
            return self.ignore(run, message, "no plan is being built")
        return run.with_(workout_slot=payload), []

    def on_stay_pick(self, run: Run, message: Message) -> Result:
        payload: StayPickPayload = message.payload
        if run.phase in (Phase.GEOCODING, Phase.DELIVERED):
            return self.ignore(run, message, "no plan is being built")
        return run.with_(stay_pick=payload), []

    def on_preference(self, run: Run, message: Message) -> Result:
        """Only the Concierge writes learned preferences to memory (best effort: a failure never stops the run)."""
        payload: PreferenceLearnedPayload = message.payload
        self._memory.save_preference(
            user_id=run.user_id, trip_id=run.trip_id, key=payload.key, value=payload.value, evidence=payload.evidence
        )
        self._memory.log_trace(
            trip_id=run.trip_id,
            user_id=run.user_id,
            step="preference_learned",
            outcome=f"{message.sender.value} learned {payload.key}={payload.value}: {payload.evidence}",
        )
        return run, []

    # --- gate ------------------------------------------------------------------------------

    def _advance(self, run: Run) -> Result:
        """Gate the stay once the shortlist and every rostered specialist's proposal are in."""
        return self._gate_and_merge(run) if run.ready_to_gate else (run, [])

    def _gate_and_merge(self, run: Run) -> Result:
        """The budget gate, then the hotels' gym and restaurant numbers, then the ranking and the legs."""
        shortlist = run.shortlist
        if shortlist is None:
            return run, []
        gate = gate_stay(shortlist, run.trip.budget, self._reserve)
        if not gate.fits:
            return self._nothing_fits(run)
        if self._stay_only:
            return self._rank_and_deliver(run, gate)
        run, queries = self._ask_for_fits(run, gate.fitting)
        if queries or self._awaiting_fits(run):
            return run.with_(phase=Phase.RANKING), list(queries)
        return self._rank_and_route(run, gate)

    # --- stay only: the Stay agent's order is the ranking ----------------------------------

    def _rank_and_deliver(self, run: Run, gate: GateResult) -> Result:
        """Keep the Stay agent's order for the hotels the budget leaves, show the first three, and finish."""
        counting = [m.id for m in run.meetings if m.counts_for_stay]
        ranked = keep_stay_order(gate.fitting, anchor_ids=counting, top_n=TOP_N)
        chosen = next(s for s in gate.fitting if s.stay_id == ranked[0].stay_id)
        shortlist = run.shortlist.stays if run.shortlist is not None else ()
        state = run.trip.bump(stay=chosen, stay_shortlist=shortlist, ranked_stays=ranked)
        text = render_top_stays(
            state,
            run.meetings,
            notes=skipped_notes(run.trip, gate, reserve=self._reserve),
            warnings=run.warnings,
            reserve=self._reserve,
        )
        self._memory.save_trip_state(state)
        self._memory.log_trace(
            trip_id=run.trip_id,
            user_id=run.user_id,
            step="ranked_stays",
            outcome="top " + ", ".join(f"{r.rank}. {r.stay_id} ({r.average_commute_minutes} min)" for r in ranked),
        )
        return run.with_(state=state, phase=Phase.DELIVERED), [reply(run, text)]

    # --- ranking: what the Gym and Restaurant agents say about each hotel -------------------

    def _ask_for_fits(self, run: Run, fitting: Sequence[StayOption]) -> tuple[Run, list[Message]]:
        """Ask each rostered Gym and Restaurant seat about the hotels it has not been asked about yet."""
        queries: list[Message] = []
        for seat in _FIT_SEATS:
            if seat not in run.roster:
                continue
            asked = run.queried.get(seat, frozenset())
            points = hotel_points([s for s in fitting if s.stay_id not in asked], run.trip.zones, run.stay_pick)
            if not points:
                continue
            query = hotel_query(run.trip, seat, points)
            queries.append(query)
            run = run.with_(
                queried=put(run.queried, seat, asked | {p.stay_id for p in points}),
                requests=put(run.requests, seat, query),
            )
        return run, queries

    @staticmethod
    def _awaiting_fits(run: Run) -> bool:
        """True while a rostered seat has been asked about a hotel it has not answered."""
        return any(
            run.queried.get(seat, frozenset()) - set(Pipeline._fits(run, seat))
            for seat in _FIT_SEATS
            if seat in run.roster
        )

    @staticmethod
    def _fits(run: Run, seat: Seat) -> dict:
        return dict(run.gym_fits if seat is Seat.GYM else run.restaurant_fits)

    def on_gym_fit(self, run: Run, message: Message) -> Result:
        payload: GymFitPayload = message.payload
        if run.phase is not Phase.RANKING or Seat.GYM not in run.roster:
            return self.ignore(run, message, "not waiting for gym distances")
        return self._after_fit(run.with_(gym_fits={**run.gym_fits, **{e.stay_id: e for e in payload.hotels}}))

    def on_restaurant_fit(self, run: Run, message: Message) -> Result:
        payload: RestaurantFitPayload = message.payload
        if run.phase is not Phase.RANKING or Seat.RESTAURANT not in run.roster:
            return self.ignore(run, message, "not waiting for restaurant ranks")
        return self._after_fit(run.with_(restaurant_fits={**run.restaurant_fits, **{e.stay_id: e for e in payload.hotels}}))

    def _after_fit(self, run: Run) -> Result:
        """Rank once every seat that was asked has answered."""
        if run.shortlist is None or self._awaiting_fits(run):
            return run, []
        gate = gate_stay(run.shortlist, run.trip.budget, self._reserve)
        return self._rank_and_route(run, gate) if gate.fits else self._nothing_fits(run)

    def _rank_and_route(self, run: Run, gate: GateResult) -> Result:
        """Rank the hotels that fit the budget, keep the top three and build the plan around the first."""
        counting = [a.id for a in run.trip.anchors if a.counts_for_stay]
        ranked = rank_hotels(
            gate.fitting,
            anchor_ids=counting,
            gym_fits=self._fits(run, Seat.GYM) if Seat.GYM in run.roster else None,
            restaurant_fits=self._fits(run, Seat.RESTAURANT) if Seat.RESTAURANT in run.roster else None,
            weights=self._weights,
            top_n=TOP_N,
        )
        ranked, arbitration = settle_close_call(ranked, gate.fitting, anchor_ids=counting, llm=self._llm)
        chosen = next(s for s in gate.fitting if s.stay_id == ranked[0].stay_id)
        self._memory.log_trace(
            trip_id=run.trip_id,
            user_id=run.user_id,
            step="ranked_hotels",
            outcome="top " + ", ".join(f"{r.rank}. {r.stay_id} ({r.score:.2f}, {r.deciding_factor})" for r in ranked),
        )
        notes = (*skipped_notes(run.trip, gate, reserve=self._reserve), *arbitration)
        return self._route(run, run.shortlist, chosen, notes, ranked)

    def _nothing_fits(self, run: Run) -> Result:
        budget = run.trip.budget
        if run.widened:
            return self.finish(
                run,
                f"I could not find a stay within {fmt_money(budget.amount, budget.currency)}, even after widening the budget "
                "and the search area. Send me a higher budget or other dates and I will try again.",
            )
        return ask_cheaper(run, self._reserve) if run.stay_round < MAX_ROUNDS else widen_and_ask(run)

    # --- legs ------------------------------------------------------------------------------

    def _route(
        self,
        run: Run,
        shortlist: StayShortlistPayload,
        chosen: StayOption,
        notes: tuple[str, ...],
        ranked: tuple[RankedStay, ...],
    ) -> Result:
        """Merge the chosen stay and every specialist's items into the state, then ask Routing for the legs."""
        location = locate_stay(chosen, run.stay_pick, run.trip.zones)
        stay = (
            chosen.model_copy(update={"lat": location.lat, "lng": location.lng})
            if location and chosen.lat is None
            else chosen
        )
        state = run.trip.bump(
            stay=stay,
            stay_shortlist=shortlist.stays,
            ranked_stays=ranked,
            entries=merge_entries(run.proposals, run.roster),
            legs=(),
            open_conflicts=(),
            round=run.review_round,
        )
        specs = plan_legs(state, location)
        run = run.with_(
            state=state, notes=notes, merge_warnings=leg_gaps(state), pending_legs={}, leg_order=(), leg_results={}
        )
        if not specs:
            return self._send_to_verifier(run, state)
        requests = [leg_request(state, spec) for spec in specs]
        run = run.with_(
            phase=Phase.ROUTING,
            pending_legs={
                m.message_id: PendingLeg(s.from_id, s.to_id, s.depart_at) for m, s in zip(requests, specs, strict=True)
            },
            leg_order=tuple(m.message_id for m in requests),
        )
        return run, list(requests)

    def _leg_answered(self, run: Run, request_id: str, payload: RouteReplyPayload) -> Result:
        pending = run.pending_legs[request_id]
        if payload.error is not None or not payload.minutes:
            return self._leg_failed(run, request_id, pending, payload.error or "no answer")
        leg = build_leg(
            from_id=pending.from_id, to_id=pending.to_id, depart_at=pending.depart_at, minutes_by_mode=payload.minutes
        )
        return self._leg_recorded(run, request_id, leg)

    def _leg_failed(self, run: Run, request_id: str, pending: PendingLeg, code: str) -> Result:
        labels = labels_for(run.trip)
        warning = (
            f"Travel time from {labels.get(pending.from_id, pending.from_id)} to {labels.get(pending.to_id, pending.to_id)} "
            f"could not be found ({code}), so it was not checked."
        )
        run = run.with_(merge_warnings=(*run.merge_warnings, warning))
        return self._leg_recorded(run, request_id, None)

    def _leg_recorded(self, run: Run, request_id: str, leg: Leg | None) -> Result:
        run = run.with_(
            pending_legs=drop(run.pending_legs, request_id), leg_results=put(run.leg_results, request_id, leg)
        )
        if run.pending_legs:
            return run, []
        legs = tuple(found for found in (run.leg_results[mid] for mid in run.leg_order) if found is not None)
        return self._send_to_verifier(run, run.trip.bump(legs=legs))

    # --- the Verifier ----------------------------------------------------------------------

    def _send_to_verifier(self, run: Run, state: TripState) -> Result:
        message = verifier_proposal(state, reply_to=run.last_verdict_id)
        self._memory.save_trip_state(state)
        run = run.with_(
            state=state, phase=Phase.REVIEWING, awaiting=frozenset(), verifier_request_id=message.message_id
        )
        return run, [message]

    def on_verdict(self, run: Run, message: Message) -> Result:
        verdict: VerdictPayload = message.payload
        if run.phase is not Phase.REVIEWING:
            return self.ignore(run, message, "no plan is with the Verifier")
        if run.awaiting:
            return self.ignore(run, message, "already waiting for revisions to a blocked plan")
        if verdict.status is not VerdictStatus.BLOCK:
            if verdict.plan_hash != run.trip.plan_hash():
                return self.ignore(run, message, "stale: its plan_hash is not the current plan's")
            return self._deliver(run, verdict, ())
        if message.reply_to is not None and message.reply_to != run.verifier_request_id:
            return self.ignore(run, message, "stale: it answers an earlier merged plan")  # a block has no plan_hash
        run = run.with_(verdicts=(*run.verdicts, verdict), last_verdict_id=message.message_id)
        if run.review_round >= MAX_ROUNDS:
            return self._deliver(
                run, verdict, verdict.conflicts
            )  # out of rounds: the open conflicts go out as warnings
        return run.with_(awaiting=conflict_owners(verdict.conflicts, run.trip)), []

    def _revised(self, run: Run, owner: Seat, trigger: Message) -> Result:
        """A conflict's owner re-posted. When every owner has, merge again (one round later) and re-verify."""
        awaiting = run.awaiting - {owner}
        run = run.with_(awaiting=awaiting)
        if awaiting:
            return run, []
        return self._gate_and_merge(run.with_(review_round=run.review_round + 1))

    # --- delivery --------------------------------------------------------------------------

    def _deliver(self, run: Run, verdict: VerdictPayload, open_conflicts: Sequence[Conflict]) -> Result:
        state = run.trip.bump(open_conflicts=tuple(open_conflicts)) if open_conflicts else run.trip
        text = render_itinerary(
            state,
            verdict,
            meeting_addresses={m.id: m.address for m in run.intake.meetings},
            warnings=(*run.warnings, *run.merge_warnings),
            llm=self._llm,
            notes=run.notes,
        )
        self._memory.save_trip_state(state)
        self._memory.log_trace(
            trip_id=run.trip_id,
            user_id=run.user_id,
            step="delivered",
            outcome=f"{verdict.status.value} after {run.review_round} revision round(s); plan {state.plan_hash()[:12]}",
        )
        return run.with_(state=state, phase=Phase.DELIVERED), [reply(run, text)]

    # --- errors ----------------------------------------------------------------------------

    def on_error(self, run: Run, message: Message) -> Result:
        """A seat rejected one of our messages. Resend a request at most twice, then drop that seat and say so."""
        payload: ErrorPayload = message.payload
        bad = payload.bad_message_id or ""
        if bad in run.pending_geocodes and run.phase is Phase.GEOCODING:
            return self.finish(
                run, f"Routing could not read my lookup for meeting {run.pending_geocodes[bad]} ({payload.code})."
            )
        if bad in run.pending_legs and run.phase is Phase.ROUTING:
            return self._leg_failed(run, bad, run.pending_legs[bad], payload.code)
        seat = message.sender
        request = run.requests.get(seat)
        if request is None or request.message_id != bad or run.phase is Phase.DELIVERED:
            return self.ignore(run, message, "it does not answer a request of ours that is still open")
        attempts = run.attempts.get(seat, 0) + 1
        run = run.with_(attempts=put(run.attempts, seat, attempts))
        if attempts <= _MAX_RESENDS:
            again = resend(request)
            return run.with_(requests=put(run.requests, seat, again)), [again]
        return self._drop(run, seat, payload.code)

    def _drop(self, run: Run, seat: Seat, code: str) -> Result:
        if seat is Seat.STAY:
            return self.finish(
                run,
                f"The Stay agent keeps rejecting my request ({code}), so I cannot plan a stay right now. Please try again later.",
            )
        run = run.with_(dropped=run.dropped | {seat})
        notice = (
            f"The {seat.value.capitalize()} agent keeps rejecting my request ({code}), so I am planning without it."
        )
        run = run.with_(
            warnings=(
                *run.warnings,
                f"The {seat.value.capitalize()} agent was unavailable, so this plan has no {seat.value} suggestions and the hotels were ranked without that factor.",
            )
        )
        run, out = self._advance(run)
        return run, [reply(run, notice), *out]
