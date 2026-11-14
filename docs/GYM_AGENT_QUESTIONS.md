# Open questions before building the gym agent

`docs/PLAN.md` (branch `docs/hive-mind-hackathon-plan`, PR #3) is a
proposed design — its own test plan checklist marks "teammates review
the owners and the open decisions" as unchecked, so it is not yet
confirmed as agreed.

PR #1 (`feature/alternative-stays-worker`) and PR #2
(`feature/adaptive-food-agent`) are being replaced with new PRs, so this
list no longer treats them as existing code to reconcile against.
Several of the decisions those PRs made are still worth settling
explicitly for the new versions, so they're captured below as open
design questions rather than conflicts.

## 1. Which message contract?

`docs/PLAN.md` defines one shared envelope — `schema_version`, `type`,
`message_id`, `reply_to`, `trip_id`, `from`/`to`, `sent_at`, `summary`,
`payload` — built by one shared `build_message`/`parse_message` module,
with typed payloads (`request`, `proposal`, `zone_fit`, `workout_slot`,
etc.), and a thin BAND wrapper around `handle(message) -> messages`.

**Why it matters for the gym agent:** every new worker PR, including the
replacements for #1 and #2, should target this same envelope and
`handle()` shape from the start, so nobody builds against a contract
that gets replaced.

## 2. Which seat names?

`docs/PLAN.md`'s roster: `stay`, `restaurant`, `gym`. Confirm these are
final (not `gyms`, not `food`, and whether an `activities` seat is real
or not) before any worker's fixtures or `affected_agents` lists get
written.

**Why it matters:** the gym agent needs to know its own name to filter
`affected_agents` on context-change events.

## 3. Is Airbnb in or out — a policy call, not a technical one

`docs/PLAN.md`'s own 14 Nov test picked Airbnb specifically because it
works through Keenable (`search_by_map=true` cards with exact-date
totals), and ruled out Booking.com entirely. A prior exploratory run
confirmed Airbnb pages are in fact reachable through Keenable — so this
is a scraping/ToS-risk decision the team should make deliberately, not a
technical limitation to work around.

**Why it matters:** the gym agent's "near the stay" anchor depends on
what the stay worker can actually return, which depends on this
decision landing before that worker is rebuilt.

## 4. Who builds the shared Keenable+SIE page reader?

`docs/PLAN.md` has Rohan build one shared reader (Keenable fetch → SIE
extract and score) that all three specialists reuse, specifically to
avoid three independent implementations.

**Why it matters:** if the gym agent is supposed to reuse that shared
reader, it should wait for it (or build against a stub with the same
interface) rather than writing its own Keenable/SIE integration that
gets thrown away once the shared reader lands.

## 5. Which SIE model, and does it fit the demo's time budget?

`docs/PLAN.md`'s test flagged `fastino/gliner2-large-v1` (an
entity-extraction model) as broken — "no structure, HTTP 500 on longer
pages" — and settled on `Qwen/Qwen3.5-4B` for extraction and
`Qwen/Qwen3-Reranker-4B` for scoring instead.

A different GLiNER model, `fastino/GLiNER2.5-Decide` (decision/
classification, not entity extraction), was smoke-tested separately and
did return a valid result — HTTP 200 after two initial 503s — but took
**82.2 seconds** for one request.

**Why it matters:** the gym agent calls SIE once per candidate gym.
82s/call does not fit a 3-minute live demo regardless of which model is
"correct" — worth settling on the plan's tested Qwen models (or
confirming a faster path) before any worker wires this in for real.

## 6. Who wires BAND — one coordinator or four?

`docs/PLAN.md` has each of the four people individually register their
own seat at app.band.ai and ship their own thin BAND wrapper around
`handle(message) -> messages`, rather than one person building a single
coordinator that calls plain-JSON workers.

**Why it matters:** this decides whether the gym agent needs its own
BAND registration and wrapper from day one, or can stay a plain
`handle()` function until a coordinator exists.

## 7. Cognee account is shared — confirm the dataset is targeted by UUID, not name

Confirmed: the team is using one shared Cognee account/key, matching
`docs/PLAN.md`'s design ("one shared Cognee Cloud account and key for
the whole team, one session per trip").

One nuance from the plan is still worth double-checking: "a shared
dataset has to be targeted by its UUID; passing another owner's name
creates your own dataset." If any worker's code (including the gym
agent) points at the dataset by name (for example `plan_my_stay`)
instead of its UUID, it may silently create a separate, unshared dataset
under that caller's own account rather than writing into the real shared
one.

One of the replaced PRs' validation notes also mentioned pulling
`/providers/keenable` and `/providers/superlinked` credentials from
**Infisical** (a shared secrets vault) for a smoke test, mapped
in-process to env vars rather than committed anywhere. If that vault
also holds `/providers/cognee`, that's a cleaner way to distribute the
key than ad hoc `.env` sharing (which is how a real key nearly got
exposed in this repo already).

**Why it matters:** before any agent's Cognee calls are wired for real,
confirm the dataset is addressed by UUID everywhere, not by name.

## 8. What does "near the stay" anchor on before a real stay pick exists?

`docs/PLAN.md`'s own test found Airbnb's map-box cards give "no listing
URL and no street address" and detail pages show no price — so even if
Airbnb is the chosen source (see #3), the stay worker's shortlist may
not carry real coordinates.

**Why it matters:** the gym agent is specified to search "near the stay
(or near the meetings before check-in)." Without a reliable stay
location, it may need to anchor on meeting locations for longer than the
plan assumes.
