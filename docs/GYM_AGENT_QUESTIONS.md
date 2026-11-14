# Open questions before building the gym agent

Six of the eight questions raised before the 14 Nov review (message
contract, seat names, Airbnb policy, shared page reader ownership, BAND
wiring, and the stay anchor before `stay_pick`) were answered in that
review and are settled in `docs/PLAN.md`. Two remain open and are the
only ones left here.

## 1. Which SIE model *and which operation type*, and does it fit the demo's time budget?

`docs/PLAN.md`'s test flagged `fastino/gliner2-large-v1` (an
entity-extraction model) as broken — "no structure, HTTP 500 on longer
pages" — and settled on `Qwen/Qwen3.5-4B` for extraction and
`Qwen/Qwen3-Reranker-4B` for scoring instead.

A different GLiNER model, `fastino/GLiNER2.5-Decide` (decision/
classification, not entity extraction), was smoke-tested separately and
did return a valid result — HTTP 200 after two initial 503s — but took
**82.2 seconds** for one request.

The Superlinked console (`console.superlinked.com/console/models`)
splits models by **operation type**: `Encode` (embeddings —
`siglip-so400m-patch14-384`, `snowflake-arctic-embed-l-v2.0`,
`BAAI/bge-m3`), `Score` (reranking — `Qwen3-Reranker-4B`,
`Qwen3-VL-Reranker-2B`, `Qwen3-Reranker-0.6B`; `Qwen3-Reranker-4B`
matches what the plan wanted for scoring), and `Extract` (`docling`
[default], `LightOnOCR-2-1B`, `GLM-OCR`) — these three read as OCR/
document-image models, not generic webpage-text field extraction.
Neither `Qwen/Qwen3.5-4B` nor any GLiNER variant appears in this
catalog view. That may just mean they're reached through a
chat-completion/`Generate`-style call (as the stays worker's
`/v1/chat/completions` usage suggested) rather than the `Extract`
operation — but it's worth confirming which operation type the shared
page reader should actually call to pull structured fields (name,
hours, price, amenities) out of scraped page text, since `Extract`
itself doesn't look like the right fit.

**Why it matters:** the gym agent calls SIE once per candidate gym.
82s/call does not fit a 3-minute live demo regardless of which model is
"correct" — worth settling on the plan's tested Qwen models (or
confirming a faster path) before any worker wires this in for real.

## 2. Cognee tenant is set up — confirm the *dataset* is targeted by UUID, not name

Confirmed and distributed privately (not in this repo, per the "no
tenant identifiers" rule `docs/PLAN.md`'s own publishing note already
set): the team has a dedicated Cognee tenant, base URL and key, matching
`docs/PLAN.md`'s design ("one shared Cognee Cloud account and key for
the whole team, one session per trip").

That settles account-level access. The narrower nuance from the plan is
still worth double-checking: "a shared dataset has to be targeted by its
UUID; passing another owner's name creates your own dataset." A tenant
ID identifies the whole account, not a specific dataset inside it — if
any worker's code (including the gym agent) points at the dataset by
name (for example `plan_my_stay`) instead of its UUID, it may still
silently create a separate, unshared dataset under that caller's own
session rather than writing into the real shared one.

One of the replaced PRs' validation notes also mentioned pulling
`/providers/keenable` and `/providers/superlinked` credentials from
**Infisical** (a shared secrets vault) for a smoke test, mapped
in-process to env vars rather than committed anywhere. If that vault
also holds `/providers/cognee` (tenant ID, base URL, key), that's the
intended distribution channel — never paste these into chat or commit
them, as happened once already with another key in this repo.

**Why it matters:** before any agent's Cognee calls are wired for real,
run one live call against `plan_my_stay` and confirm the response's
dataset identifier matches what the rest of the team expects, rather
than assuming the name alone resolves correctly.
