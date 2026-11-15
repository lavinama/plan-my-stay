# Stay Worker (standalone)

Built from `Rohan/stay-agent-spec.md` (Stay Agent Spec v4). This is the P0
stub from the spec's own build order: "Stub `handle()` returns 6 fixture
stays. BAND round trip." No Keenable call yet.

## Integration boundary

Call `handle(message, *, discover=None) -> list[dict]`. `discover` defaults
to `workers/stay/fixtures.discover_stays`, which returns 6 fixed, clearly
fictional demo stays with zero network calls.

Per D4 in the spec: Stay sends all 6 stays in **one `stay_shortlist`
message to Restaurant, Gym and Concierge** — it does not pick a winner and
does not re-rank on feedback. Restaurant and Gym report their own findings
straight to the Concierge, which picks the final 3.

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
  recorded driving time is ranked last, never assumed to be close.
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
SIE extract, ground-checked against page text), the routing service call
for real driving times, the listing-link lookup (D1), and the Cognee trace.
The spec's own cut order keeps "6 stays sent, driving times, the fixture
fallback" even if everything else is cut — that's exactly what this stub
provides.

## Run tests

```bash
python3.13 -m pytest tests/stay/ -v
```
