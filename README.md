# plan-my-stay
Given your itinerary we plan your perfect stay.

Plan My Stay is a team of agents that plans a work trip around your meetings and remembers how you
like to travel. You give a city, dates, a budget and your meetings; a Stay
agent finds and ranks stays; a Verifier can block the plan; the
Concierge writes the itinerary. You book it yourself: the agents never pay.

> **Plan change, not yet in the code (14 Nov):** `docs/PLAN.md` now has the Concierge speaking only with the Stay agent. The Restaurant agent and `restaurant_fit` are out, and the Gym agent (with `hotel_query`, `gym_fit`, `stay_pick` and `workout_slot`) is deferred, so hotels are ranked by average commute alone. The code, the demo and the rest of this README still have the Restaurant and Gym seats and a three-factor ranking until the code is changed.

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

The demo runs the New York trip (three meetings, Tue 17 to Thu 19 Nov, $1,000) through all six seats in
one process and prints the room as you would read it in Band Desktop: the Gym agent posts a workout
after meeting B (07:00 is impossible on check-in day), the Verifier blocks a restaurant that closes
before dinner ends, its owner replaces it, the Concierge re-plans and delivers the itinerary with the
fee warning shown, and run 2 starts from the preference run 1 learned. Before that, the Concierge ranks the hotels: the Stay
agent returns them by average commute, the Gym agent reports the nearest gym to each, the Restaurant agent the average rank of the
restaurants within a 10-minute walk, and the top three go to you with the numbers behind each placement. The Stay, Restaurant and Gym
seats are stand-ins (`src/plan_my_stay/rehearsal/stubs.py`) until their owners' agents land.

## Run a seat on BAND

1. Register each seat at app.band.ai (Agents, Connect remote agent) with the display name in
   `band_seats.yaml` and a description of at least 10 characters. The API key is shown once.
2. `cp agent_config.example.yaml agent_config.yaml` and paste each seat's id and key. `cp .env.example .env`
   and fill in what your seat needs (table below). Both files are git-ignored: never commit them.
3. See what is missing, by name, and start the seat:

```bash
.venv/bin/python scripts/check_env.py
```

```bash
.venv/bin/python scripts/run_seat.py concierge
```

`run_seat.py` refuses to start, naming the missing variable or config entry, if the seat cannot work.
`--offline` swaps Google, Cognee and the LLM for fixtures and in-memory fakes; `--stubs` runs a
specialist's stand-in. Then add every seat to one BAND room and message the Concierge.

The first live run settles what nothing offline can: BAND's message size limit, whether six seats fit
under one account, and how fast a Routing round trip is. `scripts/band_roundtrip.py` checks all three.

## Secrets and who holds them

| Seat | Needs | Notes |
| --- | --- | --- |
| Concierge | `COGNEE_*` (4 variables); optional `ANTHROPIC_API_KEY` | Without the Anthropic key it uses the template itinerary and only parses the demo trip |
| Routing | `GOOGLE_MAPS_API_KEY` | Enable billing, Geocoding and the Routes API; restrict the key and cap its quota. Only this seat holds it |
| Verifier | nothing | Plain code |
| Stay, Restaurant, Gym | `KEENABLE_API_KEY`, `SUPERLINKED_API_KEY`, `COGNEE_*` | Each person needs their own Keenable key: the keyless limit is per IP |

`COGNEE_DATASET_ID` must be the dataset's UUID, never its name: a name silently creates your own,
unshared dataset. No secret appears in a log line or an exception message (tests check it).

## Where things are

```
src/plan_my_stay/
  messages/    the shared contract: envelope, 12 payload types, build_message / parse_message, examples, JSON Schemas
  models.py state.py budget.py   value objects, versioned trip state with plan_hash, the budget check
  concierge/   the orchestrator: intake, budget gate, legs, itinerary, LLM client
  routing/     Google Maps behind a seat (plus a fixture client); verifier/  plain-code checks that can block
  memory/      Cognee glue (plus an in-memory fake); band_adapter/  the one BAND binding every seat imports
  rehearsal/   in-process room and stand-in specialists; seats.py cli.py preflight.py  wiring and launcher
scripts/       run_demo.py  run_seat.py  check_env.py  band_roundtrip.py  export_contracts.py
```

## Building your agent (Rohan, Jwalin, Prathik)

Write `handle(message) -> messages` and expose `build(settings)` from `src/plan_my_stay/agents/<seat>/`.
Build every message with `build_message`, round-trip the examples in `src/plan_my_stay/messages/contract_examples/`,
and reuse `plan_my_stay.verifier.hours.is_open_during` for opening hours. To see your agent deliver an
itinerary, pass it in place of its stub: `build_rehearsal_seats(overrides={Seat.GYM: build(settings)})`.
The Stay agent also sets `average_commute_minutes` on each hotel and ranks by it; the Gym agent answers a `hotel_query` with a
`gym_fit` (the nearest gym per hotel); the Restaurant agent answers it with a `restaurant_fit` (the average rank of the restaurants
within the walk radius). A ready-to-paste brief is in [docs/BUILD_IN_10_MINUTES.md](docs/BUILD_IN_10_MINUTES.md).

## What is and isn't verified

About 1,070 tests pass offline (98% line coverage), and none needs the network or a key. Nothing has run
against live BAND, Cognee, Google or Anthropic: every vendor detail not settled by the docs is marked
`# UNVERIFIED:` (`grep -rn UNVERIFIED src scripts`), and the first live round trip is where they get checked.
Not built: timeouts when a seat never answers, grid scoring of zones (v1 uses static zones), the
extensions in docs/PLAN.md, and the live Stay, Restaurant and Gym agents.
