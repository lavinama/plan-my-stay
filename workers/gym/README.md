# Gym Worker (standalone)

The gym worker finds a workout slot around the trip's meetings and, once a
stay is picked, around the stay instead. It works without BAND: a message
dict in, a list of message dicts out. A teammate can later connect it
through BAND's shared `band_adapter` module and the shared message
envelope, once that module exists.

## Integration boundary

Call `handle(message, *, discover=None, fetch_and_classify=None, clock=None) -> list[dict]`
from `workers/gym/handle.py`. `message` is one parsed envelope dict per
`docs/PLAN.md`'s shared shape (`schema_version`, `type`, `message_id`,
`reply_to`, `trip_id`, `user_id`, `state_version`, `from`, `to`, `sent_at`,
`summary`, `payload`). The return value is zero or more envelope dicts this
seat wants sent, built with the same shape.

This seat owns five message types: `request`, `stay_pick`, `conflict`,
`preference_learned` and `route_reply` (a stub for now). Anything else
either gets an `error` reply (an unknown type or an unsupported major
`schema_version`) or no reply at all (a known type this seat doesn't act
on, BAND's `band_no_reply` case). An incoming `error` message never gets
answered with another error.

Run it directly, no BAND required:

```python
import json
from workers.gym.handle import handle

message = json.load(open("workers/gym/fixtures/requests/demo_request.json"))
for reply in handle(message):
    print(reply["type"], reply["summary"])
```

## `envelope.py` is a stand-in, not the real thing

`docs/PLAN.md` calls for one shared `build_message` / `parse_message` /
validation module that every seat imports, built once by Mario. That module
doesn't exist yet, so `workers/gym/envelope.py` reproduces its contract
(field names, `KNOWN_TYPES`, validation rules, the `error` shape) so this
worker can be built and tested against the agreed envelope now. When the
real shared module lands, swap the `from workers.gym import envelope`
import at the top of `handle.py` for the real one; every function name here
(`build_message`, `parse_message`, `validate_incoming`, `error_for`,
`KNOWN_TYPES`) matches it on purpose so that swap is one import line, not a
rewrite.

## Discovery is fixture-only for now

`handle()`'s `discover` and `fetch_and_classify` keyword arguments default
to the functions in `workers/gym/fixtures.py`, which read
`workers/gym/fixtures/gyms/*.json` and do no network calls. That keeps the
worker runnable with zero credentials and zero flakiness for the demo.

This is the exact P1 swap-in point. `docs/PLAN.md`'s plan is "Keenable
fetch with a prompt as a stand-in" until Rohan's shared page reader lands,
then the real pipeline: Keenable `search_web_pages` to find candidate gym
pages per zone, `fetch_page_content` to read them, and a small model on
SIE (`Qwen/Qwen3.5-4B`) to fill the schema (name, address, hours, class
types, day-pass price). Replace the two functions passed into `handle()`
with real adapters that keep the same signatures
(`discover(zone, context) -> list[dict]`,
`fetch_and_classify(candidate, profile) -> dict`), and nothing in
`handle.py`, `slots.py` or `fit.py` needs to change.

## Fail-closed, always

This worker never fabricates a fact. `fit.py`'s hard filters
(`gym_type`, `hours`) only block a candidate when a stated fact actually
conflicts with the request; an absent fact never counts as a match and
never counts as a mismatch -- it's noted in that candidate's `missing` list
and the candidate is ranked below any candidate with a confirmed fit.
`fit.build_item` labels fixture-sourced items as using cached fixture data
in `fit.reason`, matching `docs/PLAN.md`'s own "a cached snapshot, labelled
as cached" fallback language, so nothing downstream mistakes a fixture for
a live result.

## The core algorithm (`slots.py`)

Pure, I/O-free: given a day's open window and that day's meetings, it finds
the free gaps (buffered 30 minutes on any side touching a meeting), drops
the gap before the day's first meeting on check-in day only (the traveler
is still arriving, not free), and picks one slot: the user's preferred time
if a gap actually contains it, else the trailing gap's start, else the
longest remaining gap, else no slot at all.

This reproduces `docs/PLAN.md`'s own demo numbers exactly: a Tuesday
check-in with meetings 11:00-12:00 and 16:00-17:00 and a 07:00 preference
lands on 17:30-18:15 (the preferred time doesn't fit any eligible gap, so
the trailing gap after the last meeting wins); a free Wednesday with one
10:00-11:00 meeting lands on 07:00-07:45 (the preference fits directly).
See `tests/gym/test_slots.py`.

## Does not write to Cognee

Per `docs/PLAN.md`: "The Concierge is the only one that writes it to
Cognee." This worker only merges `preference_learned` messages into its own
in-process, per-`trip_id` state (`workers/gym/state.py`) for use within the
same run. Persisting a preference across trips is the Concierge's job, not
this worker's.

## Run tests

```bash
python -m pytest tests/gym/ -v
```

35 tests cover the gap-finding algorithm against the demo numbers, the
fail-closed scoring and ranking, the envelope contract (valid and invalid
examples), and every `handle()` code path: the Tuesday request, the
Wednesday stay_pick, the regression guard that a stay_pick never touches an
already-finalized date, a conflict that swaps in a new candidate for the
same slot, a conflict on an unknown item, and malformed/unknown-type
messages.
