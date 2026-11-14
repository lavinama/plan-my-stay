# Seat protocol

How Mario's seats (Concierge, Routing, Verifier, the shared `band_adapter`, Cognee glue) fit
together. `docs/PLAN.md` is the plan; this file settles the details the plan leaves open so the
seats can be built in parallel and still connect. Where this file adds something the plan does
not say, it is marked **New** and listed in "Decisions to confirm with the team" at the end.

## 1. The handler contract

- A seat is an object with `handle(message: Message) -> Sequence[Outgoing]` (`src/plan_my_stay/seat.py`).
  The Concierge also has `handle_text(sender, text)` for prose from a person.
- `handle` is synchronous and gets only valid, parsed messages addressed to it. It returns the
  messages to send (`Message`, built with `build_message`) or `TextReply` (prose for a person).
  An empty list means "nothing to say": the adapter ends the turn with `band_no_reply`.
- **Never answer an `error` with an `error`** (return `[]`; the adapter ends the turn with
  `band_no_reply`). Two seats that answer each other's errors would loop forever. A message a
  seat can't handle gets one `error` with code `unexpected_type`, except an `error`, which is
  logged and dropped.
- Seats hold per-trip state in memory, keyed by `trip_id`. A restart loses it (the Concierge also
  saves a copy to memory).
- Build every outgoing message with `build_message`; never write JSON by hand. When answering a
  message, set `reply_to` to its `message_id`. Copy `trip_id`, `user_id` and use the current
  `state_version`. Every `to` is a list of seat names; BAND mentions are derived from it.
- Dependencies (memory, LLM, HTTP clients, clocks) come in through the constructor
  (`ports.py`), never imported inside `handle`. Tests use fakes; nothing in the test suite
  touches the network or needs a key.

## 2. Messages between the seats

| Flow | Message | Notes |
| --- | --- | --- |
| Person to Concierge | free text (`handle_text`) | The Concierge replies with `TextReply`. |
| Concierge to Stay / Restaurant / Gym | `request` | Stay gets `budget` and `response_type="stay_shortlist"`. Only seats in `roster` are asked. |
| Restaurant, Gym to Stay | `zone_fit` | Per zone. |
| Stay to Restaurant, Gym, Concierge | `stay_pick` | Shortlisted or chosen stay and its commute times. |
| Gym to Restaurant, Concierge | `workout_slot` | |
| Stay to Concierge | `stay_shortlist` | Ranked, within budget. Empty list = nothing fits. |
| Specialists to Concierge and Verifier | `proposal` | `payload.agent` is the sender. |
| Any specialist to Concierge (+ affected) | `preference_learned` | Only the Concierge writes it to memory. |
| Any seat to Routing | `route_request` | See section 3. |
| Routing to the requester | `route_reply` | `reply_to` = the request's `message_id`. |
| **New** Concierge to Verifier | `proposal` with `agent="concierge"` and `payload.state` | The merged plan. See section 4. |
| Verifier to the owning seat | `conflict` | One per conflict. |
| Verifier to Concierge | `verdict` | `clear`, `warn` or `block`. |
| Any seat to a sender | `error` | One answer to every rejected message. |

## 3. Routing (plain code, Google Maps)

- `route_request`: `origin`, `destination` (each a `Place`: an address, coordinates or both),
  `depart_at` (aware datetime) and `modes`.
- `route_reply.minutes`: minutes per mode (float, one decimal; `null` when that mode has no route).
  `route_reply.origin` / `destination` echo the places **with resolved coordinates**.
- **New: geocode-only lookup.** `modes=[]` means "just resolve the addresses". Routing replies with
  `origin`/`destination` resolved and no `minutes`. The Concierge uses this to turn each meeting's
  address into an anchor's `lat`/`lng` (it sends origin = destination = the address).
- Failures are `route_reply.error` (a short code such as `geocode_failed`, `no_route`,
  `quota_exceeded`, `upstream_error`), never an exception and never a silent turn.
- Routing caches every geocode and route result (key: normalized places + mode + departure time
  rounded to the hour) and holds the only Google key.
- Google specifics (from the plan, confirm in the first live call): Geocoding API; Routes API
  `computeRouteMatrix` (Distance Matrix is legacy); one request per mode; transit matrices are
  limited to 100 elements; send meeting times with the New York offset.

## 4. The merged plan and the Verifier

The plan says the Verifier "works from this exact state", but no message carries that state to
it. **New:** the Concierge sends the Verifier a `proposal` with `agent="concierge"` and
`payload.state` set to the full `TripState` (stay, `entries`, `legs`, anchors, budget).

The Verifier checks the state it is given, with plain code, and answers:

- `verdict` to the Concierge: `clear` or `warn` carry `plan_hash = state.plan_hash()` of the exact
  plan checked. `block` carries no hash and at least one conflict.
- One `conflict` message per conflict to the owning seat (`PlanEntry.agent`; stay and budget
  conflicts go to `stay`). The owner revises and re-posts a `proposal` (to concierge and verifier).

Stable rule names (`Conflict.rule`):

| Rule | Severity | Meaning |
| --- | --- | --- |
| `open_during_slot` | block | The item's `when` is not inside its opening hours that day. |
| `unknown_hours` | warn | No usable hours for that day: the Verifier cannot check. |
| `outside_slot_window` | block | The item's `when` is outside its slot's `window`. |
| `overlaps_event` | block | The item's `when` overlaps an anchor or another item. |
| `travel_time` | block | The gap between two consecutive events is shorter than the leg's chosen-mode minutes. |
| `missing_leg` | warn | Two consecutive events with a different place and no leg to check. |
| `over_budget` | block | The stay's effective total (after the fee reserve) is above the budget. |
| `fee_basis_unknown` | warn | A fee reserve was added to the stay total (`card_total_fees_unknown`). |

Behaviour settled while building the Verifier (`src/plan_my_stay/verifier/`):

- Two events are connected by a leg in either direction, **or by a leg from each of them to
  `"stay"`** (a route through the stay counts), so a clean plan doesn't warn `missing_leg`.
- A `block` verdict carries every conflict, blocking ones first and then the warnings. Only the
  blockers also go to their owners as `conflict` messages. A `warn` verdict carries the warnings.
- `Conflict` has no severity field. A seat that needs to tell blockers from warnings imports
  `plan_my_stay.verifier.SEVERITY` / `is_blocking`; the Concierge doesn't need to, because it
  treats everything in a `block` verdict as open.
- `travel_time` and `missing_leg` are filed under the later event of the pair; budget rules under
  `stay.stay_id`; a pair of overlapping items yields one conflict on the later-starting item.
- A Concierge `proposal` with no `state` gets an `error` (`missing_state`, field
  `payload.state`), never silence. `fee_basis_unknown` fires whenever the reserve is applied.

The Concierge delivers only a plan whose current `plan_hash()` equals the verdict's, and shows any
warning to the user. After `round` 2 it delivers with the open conflicts shown as warnings.

## 5. The Concierge's run

1. A person writes to the Concierge. `handle_text` parses the request (an LLM with structured
   output; a fallback for the demo trip) into city, dates, budget and meetings (address + time).
   Meeting addresses stay in the Concierge; agents only get anchors.
2. Geocode each meeting address through Routing (geocode-only `route_request`).
3. Build `TripState`: anchors (`counts_for_stay` is false for a meeting that starts before
   check-in), zones (a static list per city for v1), slots, roster (no Gym seat if the user skips
   workouts), profile (`MemoryPort.load_profile`). Save a copy to memory.
4. Send `request`s to the roster. Stay also gets `budget`.
5. Collect `stay_shortlist`, `proposal`s, `workout_slot`, `stay_pick`, `preference_learned`. Peer
   messages go between specialists directly; the Concierge only sees what is addressed to it.
6. Check the top stay against the budget with `check_budget`. If it is over, ask Stay for
   cheaper options (`request`, `round + 1`) or widen the zone or budget, and say so on screen.
7. Ask Routing for the legs (stay to and from each later anchor, between events), choose the
   fastest mode per leg (plain code, no LLM), merge everything into `TripState`, bump
   `state_version` and send the merged plan to the Verifier.
8. On `verdict`: `clear` or `warn` with a matching hash, so write the itinerary (frontier model,
   with a template fallback) and send it with `TextReply`. `block`, so wait for the owners'
   revised proposals, re-merge (`round + 1`, at most 2), send again.
9. Write learned preferences and the trip-state copy to memory. Accept or reject feedback from the
   user is written as plain text with its reason.

The Concierge is the only writer of preferences and trip state to memory. Memory is best effort:
if it is down, planning continues and the itinerary says nothing about it.

## 6. The `band_adapter`

Built against band-sdk 9.2.1 by reading its source (nothing has run against live BAND).

- One module every seat imports: `run_seat(seat_name, seat, *, config_path="agent_config.yaml", directory)`.
  `seat_name` is the seat's value (`"stay"`) and also its key in `agent_config.yaml`. To start one:
  `asyncio.run(run_seat("stay", seat, directory=SeatDirectory.load("band_seats.yaml")))`. It builds a
  LangGraph graph with one non-LLM node that parses the input, calls `handle` (in a worker thread,
  one call at a time per seat process), posts the answers with `band_send_message` (mentions from
  the message's `to`) and always ends the turn (an answer, or `band_no_reply`).
- **The SDK differs from the quickstart.** In 9.2.1 the graph's last message is a
  `## Current input messages (N)` block with one JSON record per line (`sender_name`, `content`, ...),
  not `[Sender Name]: content`. `inputs.py` parses both. A failed `band_send_message` returns an
  error ToolMessage instead of raising, so the adapter checks every send; if all fail it posts an
  error event and ends with `band_no_reply`. The SDK's tool-call echo is turned off (it repeats the
  whole body into the room).
- **Prose.** A body that is not JSON goes to `handle_text` only when it comes from a person. Prose
  from another seat is an `error` (`invalid_json`), so two seats can't talk to each other in prose.
- **Rejected messages** get one `error` reply (code, `bad_message_id`, fields). A sender that keeps
  sending the same bad message gets the original answer plus two more, then silence and a log line.
  A bad `message_id` that isn't a UUID still gets its answer (`reply_to` null, the id kept in the
  payload). An exception in `handle` becomes an `error` (`internal_error`); one in `handle_text`
  becomes a short apology to the person, never exception text.
- **Message size.** The SDK has no cap (only events are cut at 16,384 characters); BAND's own limit is
  unknown. Every outgoing body's size is logged; `scripts/band_roundtrip.py --probe-sizes` finds it.
- Seat names map to BAND participant names in `band_seats.yaml` (not secret). `agent_config.yaml`
  (agent ids and API keys) is secret and git-ignored.
- **Known gap: timeouts.** After the retry cap the adapter goes silent, so nothing tells the Concierge a
  specialist was dropped. The plan has the Concierge drop that agent and tell the user; that needs a
  Concierge-side timeout, which is not built.

## 7. Memory (Cognee)

`MemoryPort` in `ports.py`. Implementations: `CogneeMemory` (HTTP) and `InMemoryMemory` (tests and
offline runs); one contract suite runs against both, including a restart (a fresh memory over the
same stored data). One Cognee user for the team; the dataset is addressed by UUID (the parameter
names are from docs.cognee.ai, `# UNVERIFIED` against a live tenant); one session per trip
(`session_id = trip_id`); each entry is one line starting `agent=<name> kind=...`, so free text can't
forge another entry; per-user privacy rests on the `user=` label, which every read filters on exactly.

- Writes and `consolidate` raise `CogneeError`; reads never do (a warning and an empty result), so a
  new run always starts.
- **Run 2 in a new process needs `consolidate(user_id)` first.** `load_profile` has no trip id, so after a
  restart it only finds preferences once they have been bridged into the permanent graph (the plan's
  "between trips, when `improve` runs"). Within one process, run 2 finds them straight away. Run
  `consolidate` between the two demo runs; it takes 20 to 80 s, so do it before going on stage.
- Price facts are dropped after 24 hours; other city facts never expire.

## Decisions to confirm with the team

1. **Merged plan to the Verifier** as a `proposal` with `agent="concierge"` and `payload.state`
   (section 4). The plan has no message that carries it.
2. **Geocode-only Routing lookup** (`modes=[]`) so the Concierge can turn addresses into anchors.
3. **Static zones per city for v1.** The plan's grid scoring by Routing is not built.
4. **Fee reserve default 15%** (`FEE_RESERVE_PCT`), a placeholder the plan leaves to Mario and Rohan.
5. **Routing lookups are one message each.** If the first live round trip is slow, batch
   origins and destinations in one `route_request`.
