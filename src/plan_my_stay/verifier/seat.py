"""The Verifier seat: plain code, no LLM, and the only seat that can block the plan.

It answers one message: a `proposal` from the Concierge with `agent="concierge"` and `payload.state`
set (the merged plan; docs/SEAT_PROTOCOL.md section 4). It runs every check on that exact state and replies:

- a `verdict` to the Concierge: `clear` or `warn` with `plan_hash = state.plan_hash()` of the plan it
  checked, or `block` with no hash. The verdict lists every conflict, blocking ones first;
- one `conflict` message per blocking conflict to the seat that owns it, `reply_to` the proposal.

A specialist's own proposal is addressed to the Verifier too but needs no answer. The seat keeps no state:
everything it needs is in the message.
"""

import logging
from collections.abc import Sequence

from plan_my_stay.enums import MessageType, Seat, VerdictStatus
from plan_my_stay.messages import Message, build_message
from plan_my_stay.messages.payloads import ErrorPayload, ProposalPayload, VerdictPayload
from plan_my_stay.models import Conflict
from plan_my_stay.seat import Outgoing
from plan_my_stay.state import TripState
from plan_my_stay.verifier.checks import check_plan, is_blocking, owner_of

logger = logging.getLogger(__name__)

_SUMMARY_LIMIT = 300  # the envelope's cap on `summary`


def _one_line(text: str) -> str:
    """The text on one line, cut to the envelope's summary limit."""
    line = " ".join(text.split())
    return line if len(line) <= _SUMMARY_LIMIT else line[: _SUMMARY_LIMIT - 3] + "..."


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class VerifierSeat:
    """Checks the Concierge's merged plan and answers with a verdict and, for each blocker, a conflict to its owner."""

    def __init__(self, fee_reserve_pct: float = 0.15) -> None:
        if fee_reserve_pct < 0:
            raise ValueError("fee_reserve_pct must not be negative")
        self._fee_reserve_pct = fee_reserve_pct

    @property
    def fee_reserve_pct(self) -> float:
        """The reserve added to a stay total whose fee basis is unknown (a placeholder the team picks)."""
        return self._fee_reserve_pct

    def handle(self, message: Message) -> Sequence[Outgoing]:
        if message.sender is Seat.VERIFIER:
            logger.warning("ignoring a %s message that this seat sent itself", message.type.value)
            return []
        if message.type is MessageType.ERROR:
            logger.warning("received an error from %s: %s", message.sender.value, message.summary)
            return []  # never answer an error with an error: two seats would ping-pong forever
        payload = message.payload
        if message.type is not MessageType.PROPOSAL or not isinstance(payload, ProposalPayload):
            return [self._error(message, "unexpected_type", f"the Verifier does not handle {message.type.value} messages")]
        if payload.agent is not Seat.CONCIERGE:
            return []  # a specialist's proposal is for the Concierge to merge; the Verifier checks the merged plan
        if payload.state is None:
            return [self._error(message, "missing_state", "a plan for review must carry payload.state", fields=("payload.state",))]
        return self._review(message, payload.state)

    def _review(self, incoming: Message, state: TripState) -> list[Outgoing]:
        conflicts = check_plan(state, self._fee_reserve_pct)
        blocking = tuple(c for c in conflicts if is_blocking(c))
        ordered = blocking + tuple(c for c in conflicts if not is_blocking(c))
        status = VerdictStatus.BLOCK if blocking else VerdictStatus.WARN if conflicts else VerdictStatus.CLEAR
        logger.info(
            "trip %s state_version %s: %s (%s)",
            incoming.trip_id,
            incoming.state_version,
            status.value,
            ", ".join(f"{c.rule}:{c.item_id}" for c in ordered) or "no conflicts",
        )
        verdict = VerdictPayload(
            status=status,
            plan_hash=None if status is VerdictStatus.BLOCK else state.plan_hash(),
            conflicts=ordered,
        )
        out: list[Outgoing] = [self._send(incoming, MessageType.VERDICT, [Seat.CONCIERGE], self._verdict_summary(status, ordered, blocking), verdict)]
        out.extend(
            self._send(incoming, MessageType.CONFLICT, [owner_of(conflict, state)], conflict.detail, conflict) for conflict in blocking
        )
        return out

    @staticmethod
    def _verdict_summary(status: VerdictStatus, ordered: tuple[Conflict, ...], blocking: tuple[Conflict, ...]) -> str:
        if status is VerdictStatus.CLEAR:
            return "Plan cleared: no conflicts"
        found = "; ".join(f"{c.rule} on {c.item_id}" for c in ordered)
        if status is VerdictStatus.BLOCK:
            return f"Plan blocked by {_plural(len(blocking), 'conflict')}: {found}"
        return f"Plan cleared with {_plural(len(ordered), 'warning')}: {found}"

    @staticmethod
    def _send(incoming: Message, type_: MessageType, to: list[Seat], summary: str, payload: VerdictPayload | Conflict | ErrorPayload) -> Message:
        return build_message(
            type=type_,
            sender=Seat.VERIFIER,
            to=to,
            trip_id=incoming.trip_id,
            user_id=incoming.user_id,
            state_version=incoming.state_version,
            summary=_one_line(summary),
            payload=payload,
            reply_to=incoming.message_id,
        )

    def _error(self, incoming: Message, code: str, detail: str, fields: tuple[str, ...] = ()) -> Message:
        logger.warning("rejecting message %s from %s: %s (%s)", incoming.message_id, incoming.sender.value, code, detail)
        payload = ErrorPayload(code=code, bad_message_id=incoming.message_id, fields=fields)
        return self._send(incoming, MessageType.ERROR, [incoming.sender], f"Rejected a message: {code}: {detail}", payload)
