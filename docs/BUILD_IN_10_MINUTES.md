# Build everything in 10 minutes

`docs/PLAN.md` says what to build. This file says how to build it fast, and it starts with an honest
number: **building Mario's half of the plan here took about 45 minutes, not 10.** Below is where the time
went and a plan the measurements support. The repo now removes the two biggest costs, so the plan is
reachable the next time, and for the three teammates' agents that are still to build.

> **Scope for now (14 Nov):** the Concierge speaks only with the Stay agent (`docs/PLAN.md`), so the Restaurant, Gym, Routing and Verifier lanes, and the lane brief below, no longer apply for now: only the Stay agent is left to build, and the code does this by default. The measured build times describe the original build, whose full pipeline is still in the code behind `--full`.

## What "10 minutes" can cover

**Can be in 10 minutes:** the code for all six seats with tests green, and the New York demo trip running end
to end on fixtures with no keys (`scripts/run_demo.py`).

| Cannot be in the 10 minutes | Why | When |
| --- | --- | --- |
| Accounts and keys (Google billing, Keenable x4, Cognee, SIE, Anthropic, six BAND seats) | Sign-ups, billing and approvals are human and serial. The plan gives them the first 30 minutes | Before the clock. `scripts/check_env.py` lists what is still missing, by name |
| Live data quality (Keenable pages, SIE extraction, Airbnb zone cards) | Needs real keys and pages; the plan's riskiest path | After the fixture build, per owner |
| The live BAND round trip | Needs keys. It settles the message size limit, six seats under one account, Routing round-trip speed (`scripts/band_roundtrip.py`) | First thing once keys land |
| The extensions (cost ledger, lesson writer, multiplayer, calendar) | The plan says: only after the Verifier gate, peer messages and Cognee loop work | After |

## What it actually took (measured, 14 Nov)

| Phase | Wall-clock | What it was |
| --- | --- | --- |
| Read the plan, set up Python | about 4 min | `uv` was missing, the default Python was 3.15, the first editable install failed; a 3.12 venv fixed it |
| The shared contract | about 10 min, **serial** | Message models, trip state, config, ports, `SEAT_PROTOCOL.md`, demo fixtures, lane briefs. 69 tests |
| Verifier | 8 min | 126 tests, 100% coverage |
| Routing | 9 min | 125 tests, 100% |
| `band_adapter` | 19 min | 178 tests, 99%. Read the SDK source first |
| Memory (Cognee) | 19 min | 278 tests, 98%. Read the Cognee docs first |
| **Concierge** | **28 min** | 220 tests, 98%. One agent, 17 source files: the longest lane set the finish time |
| Integration after the last lane | about 5 min | Launcher, README, this file; the end-to-end test was already written |

Result: 1,068 tests, 98% line coverage, about 7,900 lines of source, the demo delivering an itinerary after
a block, a revision and a clear. Nothing has run against live BAND, Cognee, Google or Anthropic.

**Why it was not 10 minutes:**

1. **The longest lane sets the clock.** Five lanes ran at once and finished in the time of the Concierge.
   Two lanes (Verifier, Routing) were already 10-minute lanes; three were 19 to 28.
2. **About 10 serial minutes before anyone could start.** No lane can begin until the contract exists, and
   nothing parallelises that.
3. **Three lanes read vendor docs or SDK source first.** It was worth it (the BAND SDK's input format differs
   from its own quickstart; Cognee's dataset parameter has four different names), but it is minutes a 10-minute
   budget does not have.
4. **Tests first with 80%+ coverage.** Every lane exceeded it (94 to 100%). It is why the lanes merged without
   a debugging session, and also why they took longer.
5. **Two writers in one folder.** A forked session resumed copies of four lane agents, so two instances wrote
   the same folders: files were overwritten, two lanes adapted to a test suite they had not written, and
   coordination messages cost time. The lanes survived because each had its own tests, but it was waste.

## The plan: 10 minutes, given the above

**Before T+0 (people, in parallel):** keys and accounts (not on the clock). The contract exists already: it is
this repo. A fresh start is `git pull`, then the install line in the README, then `pytest`.

| Time | What happens | Done when |
| --- | --- | --- |
| T+0:00 | **Fan out** into lanes sized for about 8 minutes each (below). One writer per folder; no session may resume another's lane | Each lane has its brief and the vendor facts below |
| T+0:00 to 0:08 | **Build, acceptance test first.** `tests/rehearsal/test_demo_trip.py` already states what "done" is; each lane makes its part pass, with fakes for what it depends on | Each lane's own tests pass |
| T+0:08 to 0:10 | **Integrate.** `scripts/run_demo.py`; swap one stub for a live agent at a time with `build_rehearsal_seats(overrides=...)` | `pytest` is green and the demo ends with an itinerary |

Changes that make it hold, each from a cost measured above:

- **Split the long lanes** so none has more than about 8 minutes of work. Concierge into three: pure helpers
  (transport, gate, itinerary, intake, zones, legs), the LLM client, and the state machine (`pipeline`, `run`,
  `seat`), with the helpers' signatures fixed in the briefs. `band_adapter` into SDK-free core and `sdk.py`
  plus the round-trip script. Memory into text, views and the in-memory fake with the contract suite, and the
  Cognee client with `CogneeMemory`.
- **Give every lane the vendor facts instead of letting it read docs** (below). It removes the 19-minute lanes'
  reading time and the chance of building on the stale quickstart.
- **Write the acceptance test before fanning out.** Here the end-to-end test existed before the Concierge did,
  so integration took minutes. Do it in the contract phase.
- **Two tiers of "done".** At T+10 a lane has its demo path tested. The 80% coverage gate is the next step, not
  part of the 10 minutes. Skipping it is what makes 8-minute lanes possible, and it is the honest cost.
- **One writer per folder, enforced.** Give each lane its own worktree (`isolation: "worktree"`) and merge, or
  at least never let a second session message a running lane.

### Vendor facts for every lane (what the build found; `UNVERIFIED` means not run live)

- **BAND SDK 9.2.1.** The graph's input is a `## Current input messages (N)` block of JSON lines (`sender_name`,
  `content`), not the quickstart's `[Sender]: content`. Tools: `band_send_message {content, mentions}` and
  `band_no_reply {reason?}`; a failed send returns an error ToolMessage instead of raising. The SDK has no
  message size cap; BAND's own limit is unknown.
- **Google.** Routes `computeRouteMatrix`: `X-Goog-Api-Key` and `X-Goog-FieldMask` headers, one request per
  mode, `routingPreference` only for DRIVE, a past `departureTime` only for TRANSIT. Geocoding `ZERO_RESULTS`
  means no match. A `departureTime` offset such as `-05:00` is `UNVERIFIED`.
- **Cognee.** Dataset parameter names (docs, `UNVERIFIED` live): typed entry `dataset_id`, remember `datasetId`,
  recall `datasetIds`, improve `datasetId`. After a restart, preferences are found only after `consolidate`.
- **Anthropic.** `output_config.format` with a JSON schema, and the version header, from the docs: `UNVERIFIED`.

## The lanes

| Lane | Owner | Folder | Done when |
| --- | --- | --- | --- |
| Concierge, Verifier, Routing, Memory, `band_adapter` | Mario | `concierge/` `verifier/` `routing/` `memory/` `band_adapter/` | Built in this repo (the times above) |
| Page reader | Rohan | `reader/` | Saved Keenable pages become schema-valid items; every ZIP, price and hour is checked against the page text |
| Stay | Rohan | `agents/stay/` | `build(settings)` returns a seat; plain-code ranking; runs in the rehearsal |
| Restaurant | Jwalin | `agents/restaurant/` | Same, through the page reader |
| Gym | Prathik | `agents/gym/` | Same; posts the `workout_slot` the demo shows |

### Brief for a teammate's lane (paste into Claude Code, fill the `<...>`)

> Build the **<Stay | Restaurant | Gym>** agent for Plan My Stay. Read `docs/PLAN.md` (your row, "How agents
> share information", "Data, routing and fallbacks"), `docs/SEAT_PROTOCOL.md`, and
> `src/plan_my_stay/rehearsal/stubs.py`: your agent must send the same message types as that stub, including the hotel
> ranking ones (Stay ranks by average commute and sets `average_commute_minutes`; Gym answers a `hotel_query` with a `gym_fit`;
> Restaurant answers it with a `restaurant_fit`). You own only
> `src/plan_my_stay/agents/<seat>/` and `tests/agents/<seat>/`. Expose `build(settings)` returning an object whose
> `handle(message) -> Sequence[Outgoing]` takes one parsed message and returns the messages to send. Build every
> message with `build_message`; for opening hours reuse `plan_my_stay.verifier.hours.is_open_during`. Never answer
> an `error` with an `error`. Tests first, and tests never touch the network: save the raw Keenable and SIE
> responses as fixtures on the first successful pull. Done when every message round-trips `parse_message` and
> `build_rehearsal_seats(overrides={Seat.<SEAT>: build(settings)})` runs `scripts/run_demo.py`'s trip to a
> delivered itinerary.

## If the clock runs out: cut in this order

The extensions, then live Gym data (keep its stub: its `workout_slot` message is what the demo shows), then the
live Cognee client and the BAND SDK binding (the in-memory fake and the in-process room cover the rehearsal).
Never cut the Verifier gate or the peer messages. Keep the next-trip memory demo: it is a scored rubric line.

## Known gaps after this build

- **No timeouts.** If a seat never answers, a run stalls. The adapter goes silent after its retry cap, so
  nothing tells the Concierge a specialist was dropped, and the plan has it drop that agent and tell the user.
- **`Conflict` has no severity.** The Concierge keeps its own list of warning-level rules to know whom must revise.
- **24 `UNVERIFIED` markers** in 9 files (`grep -rn UNVERIFIED src scripts`): each is a live check to do once.
- **Meeting addresses reach Routing** because it geocodes them; they never reach the specialists or the Verifier.
