# Stay Worker (standalone)

Built from `Rohan/stay-agent-spec.md` (Stay Agent Spec v4). This is the P0
stub from the spec's own build order: "Stub `handle()` returns 6 fixture
stays. BAND round trip." No Keenable call yet.

Note: `Rohan/stay-agent-spec.md` is a local, untracked reference file in
this working directory -- it is not committed to the repo, so it won't be
there on a fresh clone. It's cited here as the context this worker was
built against, not as a shared doc anyone else can currently open.

## Integration boundary

Call `handle(message, *, discover=None) -> list[dict]`. `discover` defaults
to `workers/stay/fixtures.discover_stays`, which returns 6 fixed, clearly
fictional demo stays with zero network calls.

Per D4 in the spec, Stay sends all 6 stays in one `stay_shortlist` message
and does not pick a winner or re-rank on feedback. The spec's own text
says that message goes to Restaurant, Gym and Concierge, but per the PR
#9/#10 integration review, PR #10's current (still-draft) scope has the
Concierge as the only seat Stay talks to, so **this worker currently sends
`stay_shortlist` to Concierge only** (`to=["concierge"]`); the Concierge
queries Gym and Restaurant itself rather than receiving the shortlist
directly from Stay. If the team's plan changes, the recipients here
change with it.

```python
import json
from workers.stay.handle import handle

message = json.load(open("workers/stay/fixtures/requests/demo_request.json"))
for reply in handle(message):
    print(reply["type"], reply["summary"])
```

## What P0 actually does

- **Hard filter (FR4/D13):** drops any stay priced above `payload.budget`.
  Nights/guests aren't filtered yet since the fixture data is fixed to this
  one demo trip.
- **Ranking (FR8):** average driving minutes to the meetings that count
  toward the stay (`driving` per stay, keyed by meeting id) first, then
  price per night, then rating (descending) as tiebreakers. A stay with no
  recorded driving time is ranked last, never assumed to be close. This is
  a driving-only P0 stand-in -- the full plan ranks by the fastest of
  walking/transit/driving per meeting once Routing seat times arrive.
- **Fewer than 6 after filtering:** sends what's left plus a `note` in the
  payload, per the spec's edge cases ("send what there is, with a note").
  Never sends nothing.

## `envelope.py` is a stand-in, not the real thing

Same situation as `workers/gym/envelope.py`: the spec says to use "Mario's
shared module, used as is," but that module doesn't exist in the repo yet.
This file reproduces its contract field-for-field so the swap later is a
one-line import change.

## Not built yet (P1+, per the spec's own cut order)

Hop 1 (Keenable discovery per meeting address), Hop 2 (Keenable detail +
SIE extract, ground-checked against page text), real driving/walking/
transit times from Routing, the listing-link lookup (D1), and the Cognee
trace.

Note: Routing is a BAND seat, not an HTTP "routing service" -- once built,
the Stay agent gets real commute times by sending it `route_request`
messages over BAND (same pattern as any other inter-agent message in this
repo), not by calling out to an external routing API directly.

The spec's own cut order keeps "6 stays sent, driving times, the fixture
fallback" even if everything else is cut — that's exactly what this stub
provides.

## Run tests

```bash
python3.13 -m pytest tests/stay/ -v
```
