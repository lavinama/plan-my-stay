# plan-my-stay
Given your itinerary we plan your perfect stay.

Plan My Stay is a team of agents that plans a work trip around your meetings and remembers how you
like to travel. You give a city, dates, a budget and your meetings; a Stay
agent finds stays and ranks them by commute to your meetings; the Concierge keeps the ones your budget allows and
shows you the three best. You book it yourself: the agents never pay.

> **Scope for now (14 Nov):** the Concierge speaks only with the Stay agent. It sends the Stay agent the meetings (address and time) and the budget, keeps the order the Stay agent returns (by average commute), and answers with the top three stays. There is no Routing, Verifier, Gym or Restaurant in its conversation, so no day-by-day itinerary yet. The full pipeline (Routing, Verifier, Gym, Restaurant, a three-factor ranking) is still in the code behind `--full`; `docs/PLAN.md` keeps the Gym agent and the Verifier gate as deferred extensions and the Restaurant agent as dropped.

The Hive Mind hackathon plan is in [docs/PLAN.md](docs/PLAN.md).
The questions to settle before building the gym agent are in [docs/GYM_AGENT_QUESTIONS.md](docs/GYM_AGENT_QUESTIONS.md).
How the seats fit together is in [docs/SEAT_PROTOCOL.md](docs/SEAT_PROTOCOL.md), and how the build was
planned and timed is in [docs/BUILD_IN_10_MINUTES.md](docs/BUILD_IN_10_MINUTES.md).

## Try it now: no keys, no network

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev,band]"
```

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/python scripts/run_demo.py
```

The demo runs the New York trip (three meetings, Tue 17 to Thu 19 Nov, $1,000) with the Concierge and the Stay
agent in one process and prints the room as you would read it in Band Desktop: the Concierge sends one request,
the Stay agent answers with a shortlist ranked by average commute, and you get the top three stays with their
prices, commute times and the fee warning where the fees are unconfirmed. Run 2 starts from the preference the Stay
agent learned in run 1. The Stay agent is a stand-in (`src/plan_my_stay/rehearsal/stubs.py`) until Rohan's lands.

```bash
.venv/bin/python scripts/run_demo.py --full
```

`--full` runs the full pipeline instead: six seats, with the Gym agent posting a workout after meeting B, the Verifier
blocking a restaurant that closes before dinner ends, its owner replacing it, and a day-by-day itinerary.

## Run a seat on BAND

1. Register the seats you will run at app.band.ai (Agents, Connect remote agent) with the display name in
   `band_seats.yaml` and a description of at least 10 characters. For the scope above that is two: **Concierge**
   (`Plan My Stay Concierge`) and **Stay** (`Plan My Stay Stay Finder`). The API key is shown once.
2. `cp agent_config.example.yaml agent_config.yaml` and paste each seat's id and key (the top-level key is the seat name:
   `concierge`, `stay`). Or keep the file outside the repo and pass it with `--config`. `cp .env.example .env`
   and fill in what your seat needs (table below). Both files are git-ignored: never commit them.
3. See what is missing, by name, and start the seat:

```bash
.venv/bin/python scripts/check_env.py
```

```bash
.venv/bin/python scripts/run_seat.py concierge
```

```bash
.venv/bin/python scripts/run_seat.py stay --stubs
```

`run_seat.py` refuses to start, naming the missing variable or config entry, if the seat cannot work.
`--offline` swaps Google, Cognee and the LLM for fixtures and in-memory fakes; `--stubs` runs a
specialist's stand-in (the Stay agent, until Rohan's is built); `--full` makes the Concierge use the full pipeline.
Then add both seats to one BAND room and message the Concierge, for example "plan my demo trip".

The first live run settles what nothing offline can: BAND's message size limit, and whether your seats can be added to
a room without contact requests. `scripts/band_roundtrip.py` checks the registration and the size limit.

## Secrets and who holds them

| Seat | Needs | Notes |
| --- | --- | --- |
| Concierge | `COGNEE_*` (4 variables); optional `ANTHROPIC_API_KEY` | Without the Anthropic key it only parses the demo trip (the reply is a template either way) |
| Stay | `KEENABLE_API_KEY`, `SUPERLINKED_API_KEY`, `COGNEE_*` | Each person needs their own Keenable key: the keyless limit is per IP. The Stay agent also needs the meetings' coordinates and travel times: Google Maps through the Routing seat, or directly |
| Routing, Verifier, Gym, Restaurant | only with `--full` | Routing: `GOOGLE_MAPS_API_KEY` (billing, Geocoding and the Routes API; restrict the key and cap its quota). The Verifier needs nothing |

`COGNEE_DATASET_ID` must be the dataset's UUID, never its name: a name silently creates your own,
unshared dataset. No secret appears in a log line or an exception message (tests check it).

## Where things are

```
src/plan_my_stay/
  messages/    the shared contract: envelope, 12 payload types, build_message / parse_message, examples, JSON Schemas
  models.py state.py budget.py   value objects, versioned trip state with plan_hash, the budget check
  concierge/   the orchestrator: intake, budget gate, the top-three reply, and (with --full) legs, itinerary, LLM client
  routing/     Google Maps behind a seat (plus a fixture client); verifier/  plain-code checks that can block
  memory/      Cognee glue (plus an in-memory fake); band_adapter/  the one BAND binding every seat imports
  rehearsal/   in-process room and stand-in specialists; seats.py cli.py preflight.py  wiring and launcher
scripts/       run_demo.py  run_seat.py  check_env.py  band_roundtrip.py  export_contracts.py
```

## Building the Stay agent (Rohan)

Write `handle(message) -> messages` and expose `build(settings)` from `src/plan_my_stay/agents/stay/`. Build every
message with `build_message` and round-trip the examples in `src/plan_my_stay/messages/contract_examples/`
(`valid/stay_request.json` is exactly what the Concierge sends you). Your request carries the **meetings** (id, address,
start, end, `counts_for_stay`; never a title or attendees), the budget, the zones and the stay preferences, and no
coordinates: you find them, and the travel times. Answer one `stay_shortlist`: stays within the budget ranked by average
commute, each with `rank`, `deciding_factor`, `total_price`, `cost_basis`, `commute_minutes` per meeting id and
`average_commute_minutes` (the mean over the meetings that count). You may also send a `stay_pick` and a
`preference_learned`. To see your agent answer the Concierge, pass it in place of its stub:
`build_rehearsal_seats(overrides={Seat.STAY: build(settings)})`, then `scripts/run_demo.py`.

## What is and isn't verified

About 1,220 tests pass offline (98% line coverage), and none needs the network or a key. The Concierge seat has
connected to live BAND (registered, authenticated and served for 20 seconds); no message has gone through BAND yet, and
nothing has run against live Cognee, Google or Anthropic. Every vendor detail not settled by the docs is marked
`# UNVERIFIED:` (`grep -rn UNVERIFIED src scripts`), and the first live round trip is where they get checked.
Not built: timeouts when a seat never answers, grid scoring of zones (v1 uses static zones), the extensions in
docs/PLAN.md, and the live Stay agent.
