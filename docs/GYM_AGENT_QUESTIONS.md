# Open questions before building the gym agent

`docs/PLAN.md` (branch `docs/hive-mind-hackathon-plan`) is a proposed
design, not yet merged to `main` or confirmed as agreed by the team. It
conflicts in places with the two workers already pushed
(`feature/alternative-stays-worker`, `feature/adaptive-food-agent`).
Building the gym agent before these are settled risks throwing work
away, so listing them here for the team to answer first.

## 1. Which message contract?

`docs/PLAN.md` defines one shared envelope — `schema_version`, `type`,
`message_id`, `reply_to`, `trip_id`, `from`/`to`, `sent_at`, `summary`,
`payload` — built by one shared `build_message`/`parse_message` module,
with typed payloads (`request`, `proposal`, `zone_fit`, `workout_slot`,
etc.).

Both pushed workers instead use their own ad hoc JSON contracts
(`stay.search.v1` → `stay.results.v1` in `alternative_stays/worker.py`;
`food.replan.v1` → `food.results.v1` in `food/worker.py`), with no BAND
wrapper at all.

**Why it matters for the gym agent:** its request/response shape, and
whether it needs a BAND wrapper around `handle()`, depends entirely on
which of these wins.

## 2. Which seat names?

`docs/PLAN.md`'s roster: `stay`, `restaurant`, `gym`.

`workers/food/fixtures/replan_after_stay_change.json` lists
`"affected_agents": ["food", "gyms", "activities"]` — different spelling
for two of the three, plus an `activities` seat the plan never defines.

**Why it matters:** the gym agent needs to know its own name to filter
`affected_agents` on context-change events, and whether `activities` is
a real fourth seat or a leftover.

## 3. Is Airbnb in or out?

`docs/PLAN.md`'s own 14 Nov test picked Airbnb specifically because it
works through Keenable (`search_by_map=true` cards with exact-date
totals), and ruled out Booking.com entirely.

`workers/alternative_stays/worker.py` does the opposite: it hardcodes
`BLOCKED_DISCOVERY_HOSTS = ("airbnb.com", "booking.com")` and its README
states it "intentionally does not scrape or automate Airbnb or
Booking.com."

**Why it matters:** the gym agent's "near the stay" anchor depends on
what the stay agent can actually return, which depends on this.

## 4. Who builds the shared Keenable+SIE page reader?

`docs/PLAN.md` has Rohan build one shared reader (Keenable fetch → SIE
extract and score) that all three specialists reuse, specifically to
avoid three independent implementations.

`workers/alternative_stays/worker.py` already has its own complete,
independent Keenable + SIE integration (`keenable_discover`,
`keenable_fetch`, `sie_classify`).

**Why it matters:** if the gym agent is supposed to reuse the shared
reader once it lands, building its own copy now duplicates exactly what
the plan is trying to avoid.

## 5. Which SIE model?

`docs/PLAN.md`'s own 14 Nov test result: "The entity model
(fastino/gliner2-large-v1) gave no structure and returned HTTP 500 on
longer pages." It settled on `Qwen/Qwen3.5-4B` for extraction and
`Qwen/Qwen3-Reranker-4B` for scoring.

`workers/alternative_stays/worker.py`'s default is still a GLiNER
variant: `SIE_MODEL` defaults to `fastino/GLiNER2.5-Decide`.

**Why it matters:** the gym agent should not default to a model the
team's own test already flagged as broken.

## 6. Who wires BAND — one coordinator or four?

Both pushed workers explicitly state BAND integration is **not**
implemented and is left to "whoever builds the coordinator"
(`alternative_stays/README.md`, `food/README.md`).

`docs/PLAN.md` instead has each of the four people individually register
their own seat at app.band.ai and ship their own thin BAND wrapper
around `handle(message) -> messages`.

**Why it matters:** this decides whether the gym agent needs its own
BAND registration and wrapper, or just the plain JSON `handle()`
function the other two workers settled for.

## 7. Has the shared Cognee key/dataset actually been distributed?

`docs/PLAN.md`: one shared Cognee Cloud account and key for the whole
team, one session per trip, and — critically — "a shared dataset has to
be targeted by its UUID; passing another owner's name creates your own
dataset."

**Why it matters:** a dataset reached by name under an individual's own
Cognee account is not the shared one. Before any agent's Cognee calls are
wired for real, we need the actual key and dataset UUID from whoever
holds them, not just a dataset name.

## 8. What does "near the stay" anchor on before a real stay pick exists?

`docs/PLAN.md`'s own test found Airbnb's map-box cards give "no listing
URL and no street address" and detail pages show no price — so even if
Airbnb is the chosen source (see #3), the stay agent's shortlist may not
carry real coordinates.

**Why it matters:** the gym agent is specified to search "near the stay
(or near the meetings before check-in)." Without a reliable stay
location, it may need to anchor on meeting locations for longer than the
plan assumes.
