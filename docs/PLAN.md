# Plan My Stay: Hive Mind Hackathon Plan

As of 14 Nov 2026

Plan My Stay is a team of agents that plans a work trip around your meetings and remembers how you like to travel.

## What we're building

You give it a city, dates, a total budget for the stay and transport, and your meetings (address and time). A Stay agent finds stays and ranks them by commute; the Concierge keeps the ones the budget allows, plans the trips between the stay and the meetings, and shows the three best stays. A Verifier checks the plan and can block it. You get one itinerary and book it yourself; the agents never pay.

The agents also remember you and get better with use. Stay preferences go into one shared profile, so the next trip starts with them already known. A lesson writer that would turn each trip into lessons is not built (see Not building).

Why the seats can't work alone:

- The meetings fix where you need to be, so the commute decides which stay is best: the Stay agent needs the meetings' coordinates and Routing's travel times.
- A stay that looks best on average can still make one trip impossible, so a separate Verifier checks the plan and can block it.
- What the Stay agent learns about you, such as a quiet street, changes the next trip's picks.

v1 assumptions, all editable:

- One city, one stay, one itinerary. The stay runs from Tuesday check-in to Thursday check-out (two nights), and a meeting before check-in doesn't constrain where it is.
- The budget is one $1,000 total for the stay plus transport. For now only the stay price counts against it, because transport cost isn't calculated yet. The Stay agent gets the budget and returns no stay priced above it.
- Transport means the trips between the stay and the meetings and between meetings. The Routing seat gives travel times for walking, transit and driving, and the Concierge proposes the best option for each trip by time.
- Stay prices are totals including fees and taxes. A total whose fee basis the site doesn't show is labelled `card_total_fees_unknown` in `cost.basis`. The budget check adds a fee reserve (a percentage the team picks) to such a total, and the Verifier answers `warn`.
- If nothing fits the budget, the Concierge widens the zone or the budget and says so on screen.

Demo trip (New York), Tue 17 Nov to Thu 19 Nov. Each meeting lasts one hour.

| Meeting | Address | When |
| --- | --- | --- |
| A | 199 Water St, New York, NY 10038 | Tue 17 Nov, 11:00 to 12:00 |
| B | 368 9th Ave, New York, NY 10001 | Tue 17 Nov, 16:00 to 17:00 |
| C | 524 Broadway, New York, NY 10012 | Wed 18 Nov, 10:00 to 11:00 |

All three are in Manhattan, so walking and transit will compete with driving on every trip. Tuesday has a four-hour gap between A and B, and Wednesday is free after 11:00.

The stay starts on Tuesday, so A comes before check-in. Assuming the traveler arrives on Tuesday and goes straight to A, A doesn't constrain the stay: its commute score uses B to the stay on Tuesday evening and the stay to C on Wednesday.

Demo user (invented values for the demo; real preferences replace them later):

- Stay: quiet street and a private bathroom
- Budget: $1,000 total for the stay plus transport

## What judges score

Judges ask one question: would you use it tomorrow? Each build choice maps to a rubric line.

| Rubric line | What we build | Proof on stage |
| --- | --- | --- |
| Production-ready | No separate UI: a user talks to the Concierge in a BAND room, and each agent starts with one command. A README, no hardcoded secrets | A teammate joins the room and plans a trip cold |
| Shared memory that learns | Cognee stores each user's preferences, a copy of the trip state and every agent's traces, and each agent recalls what applies before it searches | A preference the Stay agent learns changes its picks in the same run, and run 2 starts from remembered preferences |
| Small-model swarm | Keenable finds the pages, Superlinked SIE reads and ranks them with small models, plain code does the checks, and a frontier model only arbitrates and writes the final itinerary | None: the share of calls by model tier needs the cost ledger, which is not built (see Not building) |
| Multiplayer agents | Not built (see Not building) | None |
| Earns or saves real money | Not built (see Not building) | None |

The organizers' slides list prizes of $750, $500 and $250, plus a $500 bonus challenge at 4:00 PM. The Luma page says $2k plus credits and different times, so confirm with an organizer. We plan to the slides.

## Agent seats and how they talk

BAND's delete test: if the app still works with the room removed, BAND wasn't essential. With one specialist, the room carries the Concierge's request and the Stay agent's shortlist, the Verifier's blockable verdict, and Routing as a seat any other seat can ask; there are no peer messages between specialists until the Gym agent returns (see Extensions). The Concierge is a seat that reacts to mentions, not a script that calls agents in order.

```mermaid
flowchart TB
    You["You<br/>chats in Band Desktop<br/>books, gives feedback"]
    Concierge["Concierge<br/>top 3 stays, itinerary<br/>transport, budget, arbitration"]
    subgraph Room["BAND room"]
        direction LR
        Stay["Stay agent<br/>one booking site<br/>ranks by commute"]
        State["Trip state travels in BAND messages (versioned JSON); Cognee keeps a copy"]
    end
    Verifier["Verifier<br/>travel times, budget<br/>can block the plan"]
    Routing["Routing<br/>Google Maps times<br/>plain code, no LLM"]
    You -->|"trip + prefs"| Concierge
    Concierge -->|"top 3 stays + itinerary"| You
    Concierge -->|"request + anchors + budget"| Stay
    Stay -->|"shortlist ranked by commute"| Concierge
    Concierge -->|"merged plan"| Verifier
    Verifier -->|"conflict, owner mentioned"| Stay
    Verifier -->|"cleared plan"| Concierge
    Concierge -->|"geocode + route requests"| Routing
    Stay -->|"route requests"| Routing
    Routing -->|"times"| Concierge
    Routing -->|"times"| Stay
```

Shared: Cognee memory, Keenable, Superlinked SIE. Google Maps sits behind the Routing seat.

Highlighted lines are the Verifier's decisions: a conflict goes to the agent that owns it, and a cleared plan goes to the Concierge.

| Seat | Job | Owner | Model tier |
| --- | --- | --- | --- |
| Concierge | Talks to the user in the BAND room, holds the private itinerary, proposes the best transport mode for each trip by travel time, checks the top stay against the budget, arbitrates ties and conflicts between the other agents and explains the trade-off to the user (it keeps the Stay agent's commute order, drops the stays the budget excludes and shows the top three, see How hotels are ranked), writes the final itinerary | Mario | Frontier, for arbitration and the final write-up only |
| Stay agent | Finds stays on one site (Airbnb worked best in the 14 Nov test), one map box per zone, drops any stay priced above the budget, then ranks the rest by average commute time to the meetings that count, with plain code | Rohan | Keenable pages read by a small model on SIE (Qwen3.5-4B); the ranking is plain code, no LLM |
| Verifier | Checks the travel time between the stay and the meetings and the stay price against the budget; can block the plan | Mario | Plain code checks, no LLM |
| Routing | Answers route requests: geocodes addresses and returns travel times for walking, transit and driving at a given time; holds the Google Maps key and caches results | Mario | Plain code, no LLM |

Routing (Google Maps) is a seat: any seat asks it for travel times by message. What the agents tell each other (each is a JSON message type, defined under How agents share information):

| Message | Who to whom | What it changes |
| --- | --- | --- |
| Stay shortlist: stays within the budget ranked by average commute, with total price, commute times and the average commute | Stay to Concierge | The Concierge drops the stays that don't fit the budget (with the fee reserve), shows the top three, adds the best transport mode by time to the first, and asks for cheaper options if none fits |
| Preference learned: key, value and evidence | Stay to Concierge | The Concierge uses it in the same run and is the only one that writes it to Cognee, for later runs |
| Conflict: which rule broke and on which item | Verifier to the owning agent | The owner revises and posts again |
| Route request: origin, destination and time | Any seat to Routing | Routing replies with travel times for walking, transit and driving |

BAND counts a seat as essential when at least one of these holds:

| Condition | Where it happens in a run |
| --- | --- |
| Dependent handoff | The shortlist changes what the Concierge asks next (cheaper options if nothing fits the budget), and a conflict changes the Stay agent's next search. |
| Blockable verdict | The Concierge delivers only after the Verifier clears the plan. |
| Roster at runtime | The Concierge recruits only the agents the request needs: the Stay agent today, and the Gym agent when it returns. |
| Enforced boundary | Agents see anchors and time windows, never meeting names or attendees. |

**How hotels are ranked**

The Stay agent ranks the hotels, and it ranks them on one thing: average commute, the travel time between the hotel and each meeting that counts for the stay, to and from, by the fastest mode. The Concierge keeps that order, drops any stay the budget excludes and shows the top three. Only the first is planned trip by trip and checked by the Verifier; the others are ranked and priced. There are no other factors for now: restaurants are out and the Gym agent is deferred (see Extensions, which keeps its second ranking factor).

1. **Hard filters first.** The Stay agent drops any stay priced above the $1,000 budget and any stay outside a workable commute of the meetings after check-in; the Concierge also drops one that is over once the fee reserve is added. Meetings before check-in don't count (`counts_for_stay` is false).
2. **Rank by average commute.** Plain code in the Stay agent, no LLM. A tie goes to the cheaper stay.
3. **Show the top three and say what decided each placement** (`rank`, `deciding_factor` and the commute times on each stay in `stay_shortlist`). Stay preferences (quiet street, private bathroom) are not scored yet.

The workable-commute limit is a first guess to tune in the first full run.

Each seat runs as its own process and acts only when mentioned. Build each agent as a plain function that takes a parsed message and returns the messages to send (`handle(message) -> messages`), with one shared `band_adapter` module (built by Mario) that every seat imports: it parses the message, calls `handle()`, posts the answer and retries. Each person writes only `handle()`, so it can be tested without BAND.

**Architecture for the Verifier, routing and memory** (agreed on 14 Nov)

- **Verifier: a BAND seat running plain code, no LLM.** BAND's blockable-verdict test needs a separate seat that can stop the plan; a function the Concierge calls would not count. The BAND quickstart shows a model-free agent: a Python LangGraph graph with one non-LLM node that replies through `band_send_message`.
- **Routing: a plain-code BAND seat, not an HTTP service.** Mario runs a Routing seat that wraps Google Maps geocoding and route matrices. Seats ask it for travel times by message, so all traffic goes through BAND and no laptop needs to reach another laptop. It holds the Maps key, caches results and answers in the same message envelope as every other seat. A lookup is a message round trip, so keep lookups to the few a run needs.
- **Trip state: versioned JSON in the BAND messages.** Every message carries `state_version`, and the Concierge raises it on each change. The plain-code Verifier works from this exact state. Cognee keeps a copy for memory only, because its session read returns just the last 20 entries and its recall is text retrieval.
- **Memory: one shared Cognee Cloud account and key, one session per trip.** Cognee scopes session entries to the user, so two users don't share a session, and a shared dataset has to be targeted by its UUID. Three agents wrote to one session at once in a test with no lost entries, so every agent can write to the trip session.
- **Where agents run: each owner's laptop, joined through BAND.** All four teammates are in one BAND room. One shared Cognee key serves all of them and the Routing seat handles travel times, so everyone builds and tests in parallel.

**BAND setup**

- Mario registers each seat at app.band.ai under one account, if BAND allows it (unverified: confirm in the first round trip), so teammates need no contact requests between their seats: Concierge, Stay, Verifier, Routing (Connect remote agent; description of at least 10 characters; avoid names like "Assistant" or "Bot"). Each owner gets their seat's key privately.
- The API key shows once. Keep `agent_config.yaml` and `.env` out of git.
- Python: `uv add "band-sdk[langgraph]"` installs the latest release (9.2.1 on 13 Nov; the quickstart showed 9.1.0). The SDK shipped three major versions in two weeks, so check the changelog if something breaks. Connect with `Agent.from_config("name", adapter=...)`. A plain-code seat such as the Verifier or Routing uses `LangGraphAdapter` with a graph that has one non-LLM node; LLM seats add a model extra such as `[anthropic]`.
- An agent receives only messages that mention it, and it must first be added to the room as a participant.

## Who builds what

Four people, one specialist agent for now. Rohan builds the Stay agent and Mario the orchestrator. The Restaurant agent is out and the Gym agent is deferred (see Open decisions and Extensions), so Jwalin and Prathik have no agent of their own yet and can take work from Mario. The Stay agent follows the recipe Keenable to find pages and SIE to read them, through one page reader that Rohan builds.

| Person | Owns | First deliverable |
| --- | --- | --- |
| Rohan | Stay agent (one site for now: Booking.com or Airbnb, whichever the data spike supports) and the shared page reader (Keenable fetch, then SIE extract and score) | A stub that answers a request with a valid stay shortlist (fixture stays) within about 45 minutes, then a first page reader, then live data |
| Jwalin | No agent now (the Restaurant agent is out). Proposed, to confirm: the shared page reader, so Rohan can stay on the Stay data. The lesson writer is not built (see Not building) | A first page reader that turns saved Keenable pages into schema-valid items |
| Prathik | No agent now (the Gym agent is deferred). The cost ledger and the calendar connection are not built (see Not building), so there is no work assigned yet | None |
| Mario | Orchestrator: Concierge seat (transport proposal, budget check, arbitration), BAND wiring (the shared `band_adapter` module), shared schema and trip state, Routing seat, Verifier checks, Cognee glue (preferences, a copy of the trip state, agent traces), secrets and credits (Google billing, Keenable, Cognee, BAND, SIE) | The accounts and keys in the first 30 minutes, then the shared JSON message models with example messages, and one @mention round trip that carries a validated message |

Shared work and who owns it:

| Work | Why it matters | Owner |
| --- | --- | --- |
| Shared page reader: Keenable fetch, then SIE extract and score | The Stay agent depends on it now and the Gym agent will later, so build it once | Rohan (Jwalin proposed) |
| Verifier checks | BAND meaningful use and production-readiness | Mario |
| Cognee glue: preferences, a copy of the trip state, agent traces | Rubric: memory that learns | Mario |
| Secrets and credits: Google billing, Keenable, Cognee, BAND, SIE | Everyone is blocked without them. Keenable's keyless limit is per IP and the venue WiFi probably shares one, so each teammate needs a Keenable key | Mario, in the first 30 minutes; share keys privately and never commit them |
| README and setup notes | Production-ready: a teammate can start every agent cold | Not yet assigned; first free person |
| Rehearse the New York demo trip end to end | A real run beats a polished demo | Mario |
| Cost ledger and lesson writer | Not built (see Not building) | None |

Rohan has the riskiest data path and now the page reader, so the reader has to land early. Until it does, the Stay agent can use Keenable's fetch with a prompt as a stand-in. Mario owns the most (Concierge, Routing seat, Verifier, Cognee glue), so the cost ledger and the lesson writer are not built (see Not building). If the team falls behind, the extensions are already out, and there is no cached-data fallback for live stay data (see Not building). Keep the next-trip memory demo: shared memory that learns is a scored rubric line. Never cut the Verifier gate.

## How agents share information

Every machine message between the Concierge and the specialists is one JSON object, built and parsed by the same shared code, so no agent ever has to read free text from another. Agree on the message models in the first 30 minutes and keep them in one shared module in the repo.

**The envelope.** Every message has the same outer shape; only `payload` changes with `type`. This example is a request to the Stay agent (illustrative values, not real results).

```json
{
  "schema_version": "1.0",
  "type": "request",
  "message_id": "6f1c2a52-6d0e-4c1b-9a53-1f0f6b2a9e10",
  "reply_to": null,
  "trip_id": "7c3f1d2e-5a4b-4e8a-9b6d-2f1c0a8e4b73",
  "user_id": "demo-user-1",
  "state_version": 2,
  "from": "concierge",
  "to": ["stay"],
  "sent_at": "2026-11-14T16:05:00-05:00",
  "summary": "Find stays in New York, Tue 17 Nov to Thu 19 Nov, within $1,000",
  "payload": {
    "city": "New York, NY",
    "dates": {"check_in": "2026-11-17", "check_out": "2026-11-19"},
    "guests": 1,
    "anchors": [
      {"id": "A", "lat": 40.7066, "lng": -74.0053, "start": "2026-11-17T11:00:00-05:00", "end": "2026-11-17T12:00:00-05:00", "counts_for_stay": false},
      {"id": "B", "lat": 40.7502, "lng": -73.9987, "start": "2026-11-17T16:00:00-05:00", "end": "2026-11-17T17:00:00-05:00", "counts_for_stay": true},
      {"id": "C", "lat": 40.7234, "lng": -73.9991, "start": "2026-11-18T10:00:00-05:00", "end": "2026-11-18T11:00:00-05:00", "counts_for_stay": true}
    ],
    "zones": [{"id": "z1", "name": "Chelsea", "bounds": {"ne": {"lat": 40.756, "lng": -73.99}, "sw": {"lat": 40.738, "lng": -74.01}}}],
    "profile": {"stay_preferences": ["quiet street", "private bathroom"]},
    "budget": {"currency": "USD", "amount": 1000},
    "roster": ["stay"],
    "response_type": "stay_shortlist"
  }
}
```

| Field | Meaning |
| --- | --- |
| schema\_version | 1.0. A new major version breaks parsing, so receivers reject an unknown major version |
| type | One of the message types below |
| message\_id | A unique id (UUID) |
| reply\_to | The message\_id this answers, or null |
| trip\_id | The trip: the BAND room id or a UUID, never built from the city and date. It is also the trip's Cognee session id |
| user\_id | The BAND user the trip belongs to. It keys the per-user profile and the Cognee dataset names |
| state\_version | The version of the trip state this message was built on. The Concierge raises it on every change |
| from, to | Seat names: concierge, stay, verifier, routing (and gym if it returns). to is a list |
| sent\_at | ISO 8601 timestamp with an offset |
| summary | One readable line, because people watch the room in Band Desktop |
| payload | The object for this type |

**Message types**

| type | From and to | Payload keys |
| --- | --- | --- |
| request | Concierge to Stay | city, dates, guests, anchors (id, lat, lng, start, end, counts\_for\_stay), zones, slots, profile, constraints, budget (the Stay agent returns no stay priced above it), roster, round (0 to 2), response\_type |
| proposal | A specialist that returns items (the Gym agent, if it returns) to Concierge and Verifier | agent, items, usage |
| stay\_shortlist | Stay to Concierge | stays priced within the budget, ranked by average commute, each with rank, deciding\_factor, stay\_id, zone\_id, total\_price, currency, commute times per anchor and average\_commute\_minutes |
| preference\_learned | Stay to Concierge | key, value, evidence |
| conflict | Verifier to the owning agent | rule, item\_id, detail, suggestion |
| verdict | Verifier to Concierge | status (clear, warn or block), plan\_hash (on clear and warn), conflicts |
| error | Any seat to the sender | code, bad\_message\_id, fields |
| route\_request | Any seat to Routing | origin, destination, depart\_at, modes |
| route\_reply | Routing to the requester | travel minutes per mode, or an error |

Each entry in a `proposal`'s `items` carries `item_id`, `slot_id`, `name`, `address`, `location_precision (exact or zone), lat`, `lng`, `when` (date, start, end), `cost` (currency, amount, basis; a stay total with no fee detail uses `card_total_fees_unknown`), `fit` (reason and the profile fields used), `hours`, `source` (url and `fetched_at`), `reader` (model and whether it escalated) and a dedupe key. Stays add commute times and the free-cancel deadline when the site shows one.

**Example stay shortlist** (Stay to Concierge; illustrative values)

```json
{
  "schema_version": "1.0",
  "type": "stay_shortlist",
  "message_id": "b7d0c9ae-3f0a-4e43-8d1e-3a4a7b3f5c21",
  "reply_to": "6f1c2a52-6d0e-4c1b-9a53-1f0f6b2a9e10",
  "trip_id": "7c3f1d2e-5a4b-4e8a-9b6d-2f1c0a8e4b73",
  "user_id": "demo-user-1",
  "state_version": 2,
  "from": "stay",
  "to": ["concierge"],
  "sent_at": "2026-11-14T16:07:12-05:00",
  "summary": "2 stays within the $1,000 budget; Chelsea ranks first on commute",
  "payload": {
    "stays": [
      {
        "rank": 1,
        "deciding_factor": "commute",
        "stay_id": "stay-chelsea-1",
        "zone_id": "z1",
        "name": "Quiet Chelsea studio",
        "total_price": 640,
        "currency": "USD",
        "cost_basis": "card_total_fees_unknown",
        "commute_minutes": {"B": {"walking": 21, "transit": 12, "driving": 9}, "C": {"walking": 38, "transit": 22, "driving": 14}},
        "average_commute_minutes": 11.5
      },
      {
        "rank": 2,
        "deciding_factor": "commute",
        "stay_id": "stay-tribeca-1",
        "zone_id": "z2",
        "name": "TriBeCa loft",
        "total_price": 780,
        "currency": "USD",
        "cost_basis": "total_incl_fees",
        "commute_minutes": {"B": {"walking": 48, "transit": 24, "driving": 17}, "C": {"walking": 15, "transit": 11, "driving": 8}},
        "average_commute_minutes": 12.5
      }
    ]
  }
}
```

**Rules that keep every message parseable**

- **One object, nothing else.** A machine message's body is exactly one JSON object: no prose, no code fence, no trailing text. The shared parser first strips what BAND adds (a leading "\[Sender\]: " prefix in some adapters, then leading @handles) and then calls `json.loads`.
- **One shared module.** Define the message models once, for example as Pydantic classes that export JSON Schema. Every agent sends with `build_message(...)` and receives with `parse_message(text)`; nobody writes or reads the JSON by hand.
- **Code builds the envelope, not an LLM.** The Concierge's model fills only the payload fields. Code adds `message_id`, `from`, `sent_at` and the rest, serializes the object and validates it before sending. Where an LLM writes payload fields, use the provider's structured output (a JSON schema) and validate the result.
- **Validate on both ends.** The sender validates before posting and the receiver on arrival. Receivers ignore unknown fields inside `payload`, so a newer sender doesn't break an older one, but reject an unknown `type` or major `schema_version`.
- **Every message gets one answer.** BAND ends a turn only on a reply or `band_no_reply`; a silent turn is marked failed and not retried. So a bad message gets an `error` message (the `code`, the `bad_message_id` and the failing `fields`), and a message that needs no answer gets `band_no_reply`. Retry a bad message at most twice; then the Concierge drops that agent from the run and tells the user.
- **Every send names a recipient.** `band_send_message` needs at least one mention, so `to` is passed as the mentions.
- **Strict values.** Timestamps are ISO 8601 with an offset; clock times are 24-hour `HH:MM`; hours maps use the keys `Mon` to `Sun` with values like "17:00-22:00" (several periods as a list), all in the trip's one time zone, America/New\_York; money is a number plus a currency code; unknown is `null`, never "n/a"; coordinates are decimal degrees; enum values are lower case and listed in the models.
- **Small messages.** Send only what the receiver needs and refer to the rest by id. BAND's docs state no length limit, so check the real limit in the first round trip; the trip state travels in the messages and Cognee keeps a copy.
- **A readable room.** Band Desktop shows these messages as raw JSON, so every message carries a one-line `summary`. The final itinerary to the user is prose from the Concierge, not JSON.
- **Contract tests.** The repo holds one valid example of every type and a few invalid ones (a missing field, a wrong type, an unknown version). Each agent's tests round-trip them through `parse_message`, and each stub answers with those examples first.

**Revisions and clearance.** A conflict sends an item back to its owner at most twice (`round` 0 to 2); after that the Concierge delivers the plan with the open conflict shown as a warning. The Verifier answers `clear`, `warn` (something it can't check, such as an unknown opening time) or `block`, and a `clear` or `warn` verdict carries the `plan_hash` of the exact plan it checked. The Concierge delivers only a plan whose hash matches, and shows any warning to the user. Start with exactly these fields; add more only when a real run needs them.

**Trip state (carried in the BAND messages as versioned JSON; Cognee keeps a copy for memory)**

- Anchors and time windows
- Budget and the stay price so far
- Each agent's shortlist and chosen item
- Open conflicts
- `state_version`, which the Concierge raises on every change

Each agent ships a stub that returns valid example messages with fixture data first, so the Concierge is built against it and swapped to live data later. Save the raw response as a fixture on the first successful pull. Fixtures also make the cost comparison fair, because live prices drift between runs.

## Data, routing and fallbacks

The Stay agent finds pages with Keenable and reads them with Superlinked SIE. Stays are the risky part, because booking sites resist automation.

**The shared page reader (Rohan builds it, Jwalin is proposed to take it over; the Gym agent reuses it if it returns)**

1. Keenable `search_web_pages` finds candidate pages for each zone. Keep recent pages with `acquired_after`; many local pages show only an acquired date, not a published date.
2. Keenable `fetch_page_content` returns each page as markdown, at its default length (a shorter cap can cut off the footer). Its `prompt` option makes Keenable's own model extract the fields; use that only as a fallback or a benchmark, so SIE does the reading.
3. Strip images and links, then a small model on SIE (`Qwen/Qwen3.5-4B` through `/v1/chat/completions`) fills the schema: name, address, hours, price, class details, rating, cancellation terms. Prompt it to copy only from the page and return null when a value isn't shown, and make price level and class types optional. Then `score` ranks the items against the user's preferences. Use an OCR model for timetables that are PDFs or images.
4. Google Maps geocoding turns the address into lat/lng, because pages carry no coordinates.
5. Store the source URL and `fetched_at` with every field, so the Verifier can flag what SIE could not read.

**What a Keenable test showed (14 Nov, restaurants and gyms only).** Generic Manhattan searches returned opening hours and dietary notes (for example a fully vegan menu) from restaurant sites and map pages, weekly hours and addresses for several restaurants on one Keenable place card, and gym day-pass prices from gym sites and a price aggregator dated October 2026. A fetch with a prompt returned JSON with nulls for fields the page didn't state, but gave one gym's address only as "New York City". Expect missing addresses and gluten-free details, and fall back to a place card or a second page. Stays were not tested. Restaurants are out of scope and gyms are deferred (see Open decisions and Extensions); the restaurant and gym results here and below stay as evidence of how the reader behaves on local-business pages.

**What an SIE extraction test showed (14 Nov, 16 pages: 7 restaurants and 9 gyms or studios, 112 field checks scored by hand).** `Qwen/Qwen3.5-4B`, asked for JSON, got 106 of 112 fields right (95%). `Qwen/Qwen3.8-27B-FP8` got 101 (90%) with the same prompt, because it filled in price levels and class types the pages didn't state. With a tightened prompt (copy only from the page, omit what isn't shown) the 27B reached 110 of 112, while the 4B gained nothing. Cost and speed were close: the 4B took a median 1.8 s and 39 credits per page, the 27B 2.5 s and 43. When a page stated a value, the 4B nearly always got it (prices 8 of 8, dietary tags 6 of 6, full addresses 7 of 7). Its failures: ZIP codes invented when the page had none, a closing time misread ("until 12:30am"), a member-only price taken as a drop-in price, and a price level that wasn't on the page. Other drift: day keys as "Monday" instead of "Mon", capitalised dietary tags, and meal-period hours left as split ranges. The entity model (`fastino/gliner2-large-v1`) gave no structure and returned HTTP 500 on longer pages. Strip images and links, fetch at Keenable's default length (a 20,000-character page is about 5,000 tokens and fits the 4B's context), and ground-check every extracted ZIP, price and hour against the page text. This is one city and 16 pages: a sample, not a benchmark.

**Stays (Rohan).** A 14 Nov test found that Airbnb works through Keenable and Booking.com does not.

- **Airbnb:** Keenable `fetch_page_content` with `live` set to true on an Airbnb search URL (check-in and check-out dates, one adult, USD) returned about ten cards with exact-date totals such as "$159 for 2 nights", plus a rating, review count, bed and bath counts and a tagline, in one to two seconds. No CAPTCHA, login or consent wall appeared. Cards have no listing URL and no street address, and detail pages showed no price, so fee basis, taxes and cancellation terms are unknown.
- **Map bounds give zones:** adding `search_by_map=true`, `zoom=14` and `ne_lat`, `ne_lng`, `sw_lat`, `sw_lng` to the URL returned cards for that box, labelled by neighborhood (Chelsea, TriBeCa, The Financial District) with landmarks in the tagline. So query one box per candidate zone from the routing tool, and treat each card's location as its zone plus the tagline landmark.
- **Booking.com:** the search page showed property names but no usable prices, only a few sponsored hotels with very high totals. Detail pages timed out or showed no price, and identical calls gave different results. Rohan picks the site; this test favoured Airbnb.
- **Skip Keenable's `prompt` option on these pages:** it returned generic names and null fees. Fetch the raw text and parse it with SIE.
- **Filters:** keep only cards that say "for 2 nights" (nearby dates appear with other lengths), and drop cards outside Manhattan (one result was in Union City, New Jersey).

Order to try: the Airbnb map-box fetch first; a hotel-price API (Amadeus, SerpApi's Google Hotels or Booking's Demand API) if the demo needs named listings with fees and cancellation terms. A cached snapshot as a last resort is not built (see Not building). Search for candidates first and keep to the shortlist. Rohan decides the order: if the Airbnb data is too complicated to fit the proposal schema (no street address, no listing URL), he can skip it.

Keenable: the MCP server works without a key on a public tier of 1,000 requests per hour and 10 per second per IP, and a key (starts with keen\_, sent in the X-API-Key header) removes the hourly cap. The keyless limit is per IP address, and venue WiFi usually shares one, so each teammate should use a key. The Keenable deck lists 100K free requests, then $4 per 1,000, plus a $200 credit using the code in the organizers' slides (valid Nov 14 to 15, Pacific time).

**Google Maps (behind the Routing seat, Mario)**

- Enable billing now; Maps Platform needs it even for the free credit.
- Enable Geocoding and the Routes API route matrix (Google marks Distance Matrix as legacy). Places and Maps JavaScript aren't needed.
- Restrict the key, cap its quota and keep it out of git; only the Routing seat holds it.
- Send one matrix request per mode (walking, transit and driving) and compare the times. Transit matrices are limited to 100 elements, so a coarse grid of about 25 points against two meetings fits. These limits come from a reviewer's read of Google's docs; confirm them in the first call.
- Send the meeting times with the New York offset. Arrival time works only for transit.
- Maps has no best-neighborhood call: score the grid against the meetings after check-in to pick zones, then score real places exactly.
- Cache every route and geocoding result in the Routing seat.
- Transport cost isn't calculated for now; only travel time is. The budget check uses the stay price.

## Cost and small models

Run the cheapest step that works and escalate only when a check fails. The swarm-versus-frontier comparison and the call logging behind it are not built (see Not building).

1. Zones: the Routing seat scores a coarse grid against the meetings after check-in by travel time for walking, transit and driving, plain code.
2. The Stay agent searches for pages with Keenable, one search per zone, in parallel. No model yet.
3. A small model on SIE reads each page into the schema (Qwen/Qwen3.5-4B worked in the 14 Nov test).
4. Hotel ranking: the Stay agent returns hotels ranked by average commute with plain code; the Concierge drops the ones the budget excludes (with the fee reserve) and keeps the top three.
5. When stay preferences (a quiet street, a private bathroom) are scored, the SIE reranker, such as `Qwen3-Reranker-4B`, ranks the stays against them. Not yet.
6. Validate: arithmetic, schema and ground checks (every ZIP, price and hour must appear in the page text). Failures go to the 27B model with a tight prompt, then to a frontier model; log the escalation rate.
7. The Verifier checks the merged plan with plain code.
8. The Concierge arbitrates conflicts and writes the itinerary, the only routine frontier call.
9. User feedback goes into the preferences, and the run's traces go to Cognee. The lesson writer is not built.

- **Cost ledger: not built (see Not building).** It would have wrapped every model and tool call: tokens, Keenable requests, SIE calls, Maps elements.
- **Recipes worth copying** from the [Superlinked cookbook](https://github.com/superlinked/cookbook): `route_coding_tasks_to_open_models_first` (small model first, escalate on a failed check) and `keep_facts_current_from_notes`.
- **Extensions:** the cost comparison, the quality check, the cost ledger and the lesson writer are not built (see Not building).

**Small models on SIE.** The base URL is `https://api.superlinked.com` and requests use `Authorization: Bearer <key>`; Mario holds the key. The account lists 72 models, including `Qwen/Qwen3.5-4B` and `Qwen/Qwen3.8-27B-FP8` for generation, `Qwen/Qwen3-Reranker-4B` for scoring, `lightonai/LightOnOCR-3-4B` and `docling` for documents, `fastino/gliner2-large-v1` for entities, and `superlinked/pii-redact`. Generation works through the OpenAI-compatible `/v1/chat/completions`. `/v1/extract/:model` and `/v1/score/:model` take the model id with `/` written as `__`. Responses report `credits_charged` (the cost ledger that would use it is not built). Recipe names come from a page summary; confirm them in the cookbook repo.

**SIE reranker.** `POST /v1/score/Qwen__Qwen3-Reranker-4B` with `{"query":{"text":"..."},"items":[{"text":"...","id":"..."}],"params":{"instruction":"..."}}`; a plain-string `query` returns 400. It returns `scores` sorted best first, each with `item_id`, `score` and `rank`. Item ids default to `item-N`, so map them back to your input order. It took about 0.5 s and 3 to 6 credits for 7 to 9 items. Rankings were sensible for soft fit (vegetarian-friendly restaurants, HIIT gyms) and identical when the input order was reversed. It doesn't reason about opening times, so hard constraints such as "open Tuesday at 17:30" must be code filters on the extracted hours. Use the rank, not the score: scores aren't comparable across queries.

## Memory

Cognee is how the agents remember each user and get better over time. It stores user preferences, the current trip and every agent's traces. Shared city facts and the lesson writer are not built (see Not building).

- **User preferences (personal scope):** price comfort, stay preferences, transport mode, past picks and feedback. Every agent reads them. An agent that learns a preference sends a `preference_learned` message, which the other agents use in the same run. The Concierge is the only writer to Cognee, and it writes the preference to the trip session with `self_improvement` off. Preferences would move into permanent memory between trips when `improve` runs, but nothing calls it (see Not building).
- **Trip state copy (session memory):** anchors, time windows, budget and the stay price so far, each agent's shortlist and open conflicts. The messages carry the live state; Cognee keeps a copy so a late-joining agent or a later run can recall it.
- **City facts (shared scope): not built.** `save_city_fact` and `recall_city_facts` exist in `memory/` but no seat calls them (see Not building). The design was place facts, hours, price levels, neighborhood traits and commute matrices, each stored with its source URL and `fetched_at`, with prices expiring.
- **Agent traces (lessons are not built):** every search, page read, Verifier conflict and user accept or reject is logged to the trip session as an entry. The lesson writer that would turn traces into rules, and agents recalling lessons before a search, are not built (see Not building).
- **Promotion is explicit:** only city facts and agent lessons would move to shared memory, with a person approving; neither is built (see Not building), so nothing moves. Preferences and itineraries stay private.
- **Cross-agent learning:** what one agent learns, the others use at once, through a `preference_learned` message. With one specialist, what the Stay agent learns (a rejected hotel on a noisy street) goes to the Concierge and into the next request; learning across specialists returns with the Gym agent.
- **Feedback:** an accept or reject is written as plain text with its reason, because Cognee ignores its feedback fields, and it updates the preferences.
- **Demo:** run 1 builds the preferences. Run 2, a new city for the same user, starts from them and asks fewer questions. The lessons list is not built (see Not building).

**What the Cognee calls do today** (from the Cognee docs)

| Call | What it does today | How we use it |
| --- | --- | --- |
| `remember` | With no `session_id`, stores data as permanent graph memory: it ingests, builds the graph, then runs `improve`. With a `session_id`, writes to a fast session cache keyed by user and session, and bridges it into the permanent graph in the background unless `self_improvement` is off | City facts are not built (see Not building). Preferences learned in a run, a copy of each trip's state and the agent steps go in as session entries; bridging the preferences to permanent memory between trips needs `improve`, which nothing calls |
| `recall` | Searches the session cache first, then the permanent graph, and tags each result `session` or `graph`. `scope=["trace"]` searches recorded agent traces; `scope=["session_first"]` checks the session before the graph | Agents read preferences before each search; city facts and lessons are not built |
| `improve` | Bridges sessions into the permanent graph, turns agent trace steps into documents, distills lessons under `session_learnings`, re-weights graph edges from feedback and flags conflicting facts. Tested on 14 Nov: 20 to 80 s per call, one generic lesson after the first run and none after later runs, feedback ignored | Not called (see Not building). It would run between trips, never in a request, to bridge session text into the graph; don't rely on its lessons |

What this means for our setup:

- **One Cognee user for the team.** Session entries are scoped to the user, so the same session id under two users isn't shared. A shared dataset has to be targeted by its UUID; passing another owner's name creates your own dataset. Use one Cognee Cloud account and key for all agents. Because every traveler shares that one Cognee user, per-user privacy rests on naming (the `user_id` in dataset and entry names), not on Cognee's own scoping.
- **One session per trip,** bound to that trip's dataset. Sessions expire after 7 days by default.
- **Several agents can write to one session at once.** In a test, three agents wrote 18 of 18 entries with no losses or errors, in one shared session and in three separate ones. Entries carry no author, so each agent starts its entries with `agent=<name>`. Session detail returns only the last 20 QA and 20 trace entries, so read a long session through `recall`.
- **`improve` is slow and its lessons are weak.** It takes 20 to 80 s per call. On this tenant it wrote one generic lesson after the first run (check venue pages, verify hours separately) and none after runs two and three, it ignored feedback fields, it skipped feedback weighting because the graph backend doesn't support it, and its preference edges did nothing.
- **What does work:** write preferences and rules as plain conversational QA text, run `improve`, then use plain `recall`. It answered "reject places that close at or before 9 pm or are more than a 12-minute walk away; accept an explicit vegetarian menu". That is retrieval over text, not learned lessons, which is why we write the lessons ourselves.
- **Only some fields reach the graph:** the QA question and answer, and a trace's function name, status and return value. `feedback_text`, `feedback_score`, QA `context` and trace parameters don't, so put queries, outcomes and accept or reject reasons into the text or the return value.
- **`improve` costs LLM calls.** Nothing calls it, and the cost ledger is not built (see Not building).

**Cognee request shapes (tested on 14 Nov).** Headers are `X-Api-Key`, `X-Tenant-Id` and, for JSON, `Content-Type: application/json`.

- **Typed entry:** `POST /api/v1/remember/entry` with JSON `{dataset_name, session_id, entry}`. A QA entry is `{type:"qa", question, answer}`; a trace entry is `{type:"trace", origin_function, status, method_params, method_return_value, memory_query, memory_context}`; a feedback entry is `{type:"feedback", qa_id, feedback_text, feedback_score}`. It returns `session_stored`.
- **Plain text:** `POST /api/v1/remember`, multipart `datasetName`, `session_id`, `raw_data` and `self_improvement=false`. It is stored as a QA with an empty question.
- **Read a session:** `GET /api/v1/sessions/{id}`; list with `GET /api/v1/sessions?range=all`.
- **Recall:** `POST /api/v1/recall` with JSON `{query, datasets:[name]}`. Add `sessionId`, `scope` ("session", \["trace"\] or \["session\_context"\]) and `contextProfile` to search a session.
- **Improve:** `POST /api/v1/improve` with JSON `{datasetName, sessionIds:[...]}`.
- **Create and delete:** `POST /api/v1/datasets/` with `{name}`; `DELETE /api/v1/sessions/{id}`; `DELETE /api/v1/datasets/{dataset_id}`.

## Partner and rubric check

All four partners now do real work: Keenable finds the pages, SIE reads them, Cognee stores preferences and traces, and BAND carries the agent-to-agent messages. Multiplayer and the money comparison are not built (see Not building); the rest are covered or listed below.

**Partners**

| Partner | How the plan uses it | Verdict | What to confirm |
| --- | --- | --- | --- |
| BAND | The Concierge and Stay messages, the Verifier gate, Routing as a seat, runtime recruiting, Band Desktop in the demo; peer messages between specialists return with the Gym agent | Strong; plain-code seats are supported (quickstart) | Re-plan loops are capped at two rounds; the Routing seat adds one message per lookup |
| Cognee | Preferences, a copy of the trip state and agent traces, stored and recalled; city facts and lessons are not built | Storage and recall work (tested on 14 Nov); its automatic lessons were weak | `improve` is not called (see Not building) |
| Keenable | Finds and fetches pages for the Stay agent, with dated results | Strong for Airbnb search cards (tested on 14 Nov; gyms and restaurants worked too, both out of scope for now); Booking.com was not usable in the test | Whether zone-level Airbnb cards are enough for the demo; a hotel-price API if not |
| Superlinked SIE | A small LLM reads every page into the schema; the reranker scores the items | Strong (tested on 16 pages on 14 Nov: 95% of fields right) | Credits per page (about 40); ground-check every extracted ZIP and number |

**Judging criteria**

| Criterion | Status | Gap or next step |
| --- | --- | --- |
| Most production-ready | Partly | Needs the production basics below |
| Shared memory that learns | Covered by stored preferences; lessons are not built | The demo shows run 1 vs. run 2 |
| Small-model swarm | Mostly | The cost ledger and quality parity are not built |
| Multiplayer agents | Not built | See Not building |
| Earns or saves real money | Not built | See Not building |
| Cost reduction (organizers' slide) | Not built | The cost ledger and the full comparison are not built (see Not building) |

**Production basics (proposed).** A per-user profile keyed by the BAND user so two users keep separate profiles, and one test with a user outside the team. Booking deep links, an itinerary export as a calendar file, a per-run cost cap and timeouts on agent calls are not built (see Not building).

## Extensions

Do these only after the Verifier gate and the Cognee loop work end to end.

Decided on 14 Nov: the partner handoff (1), the quality-parity check (3), the calendar connection (4), and the cost ledger and lesson writer (5) will not be built. See Not building.

**1. Multiplayer: partner with other teams**

The aim is agents from different teams working on one task through BAND. Our Concierge becomes a seat other teams' agents can call, and it can call theirs.

- Publish the Concierge in the BAND registry with a clear description of what it accepts and returns, such as "plans a work trip: the best stay and the trips to your meetings, from meetings and preferences".
- Keep the public contract small: the request (city, dates, budget, anchors with time windows) and the reply (the cleared plan). Reuse our request and proposal schemas.
- Partner patterns, if other teams build the organizers' example jobs:
  - Inbox team: its agent detects a trip in an email and sends us the request; we reply with the plan.
  - Admin team: its agent takes our plan's prices and builds an expense report.
  - Hiring or research teams: an interview or meeting schedule becomes our anchors.
- Cross-team access needs a BAND contact request that both sides approve. Arrange it at kickoff or on the BAND Discord, not at the end.
- Privacy: a partner receives the plan, never the user's preferences or meeting details beyond the anchors it sent.
- Proof: a live handoff in the demo, with the BAND room showing both teams' agents.

**2. Earns or saves money: cost against a traditional LLM agent**

Compare what a run costs on this swarm with what the same trips cost on a traditional agent that sends every step to one frontier model.

- Cache the raw pages and results once, then replay both pipelines on the same inputs, because live prices drift between runs.
- Keep a price table: a list price per unit for each tool (tokens by model, Keenable requests, SIE credits, Maps elements), so every call in the ledger has a cost.
- Report cost per run for three setups: all-frontier with no memory, small models with no memory, and small models with memory. Take tokens, Keenable requests, SIE calls and Maps elements from the cost ledger.
- Add a baseline line: the same page tokens priced at a frontier model.
- Add cost per run across five runs to show memory lowering the cost over time.
- State the result as money: cost per planned trip, and the saving across 1,000 trips.

**3. Quality parity with a frontier model**

Cheaper only counts if the picks are as good.

- Report the share of calls by model tier: plain code, small model and frontier.
- Check agreement on about 30 samples: run the same inputs through the all-frontier pipeline and count how often its top pick matches ours.
- Show the escalation rate next to it.

**4. Calendar: connect a user's calendar and find the meetings**

Today the user types the meetings. With a connected calendar, the Concierge reads the events and proposes the anchors itself.

- Connect the calendar with read-only access, through a calendar API or a calendar connector if one is available. For the demo, use one test account that holds the three New York meetings.
- Read the events in the trip's date range and keep the ones with a physical location in the destination city. Skip all-day events, declined events and video calls.
- Turn each kept event into an anchor: geocode the location, take the start and end from the event, and convert time zones. Flag overlapping events.
- Suggest the trip dates from the first and last meeting days, and show them for approval. Never book anything from them.
- Show the user the meetings it found, with their times, and wait for a yes or a correction before planning.
- Privacy: only the Concierge reads the calendar. The other agents get anchors with time windows, never titles or attendees, and nothing but the anchors is carried in the trip state.
- Proof: in the demo, connect the calendar and let the Concierge list the three meetings back before it starts planning.

**5. Cost ledger and lesson writer (Jwalin or Prathik)**

These two pieces sit outside the core so Mario's list stays buildable.

- Cost ledger: wrap every model and tool call and sum the `usage` that each proposal already carries (tokens, Keenable requests, SIE credits, Maps elements) into one table with a price per unit.
- Lesson writer: after each trip, a small model reads the trip's traces and the user's accept or reject reasons and writes explicit rules as plain text into Cognee. Before its next search, each agent recalls its lessons and uses them to shape that search.

**6. Demo variations**

- Run 2 in the same city with new meetings, to show cache hits and a cheaper second run.

## Not building

Decided on 14 Nov: these will never be built. Where an earlier section of this plan describes one of them, read it as a record of what was considered, not as work to do.

- **Timeouts for a silent seat.** The HTTP clients (Google Maps, Cognee, the Concierge's model) have timeouts, but nothing handles a seat that never answers.
- **Grid scoring of zones by Routing.** v1 uses static zones only: one fixed list of zones per city.
- **Production basics:** booking deep links, a calendar-file export of the itinerary and a per-run cost cap. The only cap is the 500-call Google Maps limit in `seats.py`.
- **A cached-data fallback.** There is no "show the cached snapshot, labelled as cached".
- **Memory calls that nothing makes.** `consolidate` (Cognee's `improve`, run between trips) and the shared city-facts calls (`save_city_fact`, `recall_city_facts`) are implemented in `memory/`, but no seat calls them and none will. The other memory calls are wired in: `load_profile`, `save_preference`, `save_trip_state` and `log_trace`.
- **Scoring stay preferences.** Quiet street and private bathroom are not scored.
- **Extensions:** the cost ledger (a `usage` field exists on each proposal, but nothing sums it), the lesson writer, the partner handoff, the calendar connection and the quality-parity check.

**7. Gym agent (deferred: the Concierge speaks only with the Stay agent for now)**

Not dropped, only parked, so the design is kept here. A Gym agent (Prathik; Keenable pages read by a small model on SIE, with the SIE reranker) finds gyms near the stay, or near the meetings before check-in, picks a workout slot and matches gym preferences. It returns as a second specialist and brings back the peer messages.

- **Messages:** `stay_pick` (Stay to Gym and Concierge: the stay's location and commute times, so the Gym agent searches around it), `workout_slot` (Gym to Concierge: date, start, end and place), `hotel_query` (Concierge to Gym: the candidate hotels with coordinates, a zone's centre when a listing has no address) and `gym_fit` (Gym to Concierge: per hotel, `nearest_gym` with `commute_minutes` and `distance_m`, or null when there is none), plus a `proposal` for the workout with its hours.
- **Ranking:** the Concierge combines two factors with plain code: the commute to the meetings (0.67) and the commute from the hotel to the nearest gym (0.33). Each factor is a score from 0 to 1 as best divided by this, a hotel with no gym found scores 0 on the gym factor, and if no hotel has a gym the factor is left out. The weights are first guesses.
- **Open question (Prathik, Mario):** does the Gym agent ask Routing for travel times to its nearest gyms (exact, one message each) or estimate from straight-line distance (free)?
- **Verifier:** gains the opening-hours check, so it can block a gym that closes before the workout ends.
- **Profile and data:** gym type and preferred workout times join the profile. Run the page reader on each gym's own page and ask for opening hours, price level, the class timetable and the day-pass price.
- **Demo:** the 07:00 workout the user prefers is impossible on Tuesday, because check-in is Tuesday, so the Gym agent posts a workout after meeting B, from about 17:30. When the user skips workouts the Concierge recruits no Gym agent, which shows the runtime roster.
- The questions to settle first are in [docs/GYM_AGENT_QUESTIONS.md](GYM_AGENT_QUESTIONS.md). Restaurants stay out.

## Demo and judge questions

Three minutes, one real trip, and each BAND moment named for the condition it shows.

1. 0:00 to 0:30: the New York trip (three meetings on 17 and 18 Nov). The stored preferences already know, for example, that the user wants a quiet street and a private bathroom.
2. 0:30 to 1:30: a live run. Itinerary in. The Concierge asks the Stay agent for stays within the budget and gets them ranked by average commute to the meetings after check-in (meeting A comes before check-in, so it doesn't count). It drops any stay that is over budget once the fee reserve is added, asks Routing for the trips between the stay and the meetings, and sends the plan to the Verifier. The Verifier blocks a stay whose morning trip to meeting C would arrive after it starts, the Stay agent offers a replacement, and the Concierge shows the top three stays with the best transport mode for each trip. Say which BAND condition each moment shows, and rehearse until the block fires every time on fixtures.
3. 1:30 to 2:15: run 2 in a new city for the same user. It starts from the remembered preferences and asks fewer questions.
4. 2:15 to 2:45: not used: the cost ledger, the swarm vs. all-LLM cost table and the agreement check are not built (see Not building).
5. 2:45 to 3:00: how anyone starts it on Monday: join the BAND room and message the Concierge.

Likely questions:

- **Why do the agents need each other?** Where you sleep decides your commute to every meeting, a stay that looks best on average can still make one trip impossible so a separate Verifier checks the plan, and what the Stay agent learns about you changes the next trip's picks.
- **How is this cheaper than a normal LLM agent?** Keenable and SIE do the page finding and reading with small models, plain code does the checks, and a frontier model is used only to arbitrate and write the itinerary. The cost ledger that would show the split is not built (see Not building).
- **Terms of service.** Booking and Airbnb restrict scraping. That's acceptable for a hackathon; production would need partner or affiliate APIs. Say so before judges ask.
- **Privacy.** Itineraries are sensitive. Nothing goes to shared memory (city facts and agent lessons are not built), and agents never see meeting names or attendees.
- **What if live data fails?** There is no cached-data fallback (see Not building).

## Open decisions

The questions to settle before building the Gym agent, which is deferred, are in [docs/GYM_AGENT_QUESTIONS.md](GYM_AGENT_QUESTIONS.md).

Settled on 14 Nov:

- The budget is one $1,000 total for the stay plus transport (the user provides it in real use). For now only the stay price counts, because transport cost isn't calculated yet, and the Stay agent returns no stay above the budget.
- One traveler in one room, with a stay from Tuesday check-in to Thursday check-out (two nights).
- The Concierge proposes the best transport mode for each trip by travel time. Transport covers only trips between the stay and the meetings and between meetings.
- The demo trip is New York, with one-hour meetings. The demo plans the stay and the trips between the stay and the meetings: no workouts and no meals.
- The Restaurant agent is out: no meals are planned, and there is no `restaurant_fit` (or `zone_fit`, which only fed the old ranking).
- The Concierge speaks only with the Stay agent for now. The Gym agent is deferred, not dropped (Extensions keeps its design: `stay_pick`, `workout_slot`, `hotel_query`, `gym_fit` and a second ranking factor). Hotels are ranked by average commute alone, the travel time between the hotel and the meetings that count, to and from, and the Concierge shows the top three.
- Rohan picks the booking site, one for now, whichever the Stay agent prefers. If the Airbnb data is too complicated, he drops it.
- The Verifier and the Routing component are plain-code BAND seats, and all communication goes through BAND.
- Trip state travels as versioned JSON in the BAND messages, and Cognee keeps a copy.
- The BAND SDK is the latest release, and all four teammates are in one BAND room.
- The cost ledger and the lesson writer are not built (see Not building). The demo variations are extensions for Jwalin or Prathik.
- SIE's base URL is `https://api.superlinked.com`. Mario holds the key, and it never goes in this document or the repo.
- H Company is out, and there are no partner prizes, only the organizers' $750, $500 and $250 plus the $500 bonus challenge.
- There is no backup video and no cached-data fallback (see Not building).

Still open:

- [ ] Stay data (Rohan): Airbnb map-box cards give exact-date totals but no listing URLs, addresses, fee basis or cancellation terms. Is zone-level data enough for the demo, or do we add a hotel-price API?
- [ ] Workable-commute limit (Rohan): the hard filter on the Stay agent's ranking; set it in the first full run.
- [ ] Jwalin's and Prathik's work (Mario, Jwalin, Prathik): the Restaurant agent is out and the Gym agent is deferred. Proposed: Jwalin the shared page reader. The lesson writer, the cost ledger and the calendar connection are not built (see Not building), so Prathik has no work assigned yet.
- [ ] Routing over BAND: confirm a route lookup round trip is fast enough, since every lookup is a message.
- [ ] BAND message limits: the docs state no maximum length. Confirm in the first round trip that a full request and a full proposal arrive intact.
- [ ] Fee reserve (Mario, with Rohan): the percentage the budget check adds when a stay total's fee basis is unknown.
- [ ] BAND account (Mario): confirm in the first round trip that all four seats can be registered under one account.

## Sources

As of 2026-11-14. The primary docs were read for Keenable, BAND, Cognee (remember, recall, improve) and SIE (API reference). Restaurant and gym lookups were tested by calling the Keenable MCP endpoint on 14 Nov; nothing else was tested hands-on. The Superlinked event page, the Keenable deck and the BAND hacker guide came through a page summarizer, so re-read those originals before depending on them.

- [Hive Mind event page (Luma)](https://luma.com/cognee-alq6)
- [Hive Mind organizer slides](https://docs.google.com/presentation/d/1xU569_d_Hvd_epdDPERclsfyvi0BQdC51283RKzTr44/edit?slide=id.p1#slide=id.p1)
- [Cognee product deck](https://docs.google.com/presentation/d/1hNw-_xuHXlwwU_46bOUdFi9lOUKqLxVl92g_2We1ehI/edit?slide=id.h4146e913604ed86c_0_79#slide=id.h4146e913604ed86c_0_79)
- [Superlinked hackathon page](https://superlinked.com/hive-mind-hackathon#/2)
- [Keenable deck](https://artifacts.keenable.ai/hivemind-deck/#3)
- [BAND intro deck (Drive folder)](https://drive.google.com/drive/folders/1x-2DGnd1aBwTkzF6EFkr76BzttaQPiBS)
- [BAND hacker guide](https://band.ai/hacker-guide)
- [BAND docs](https://docs.band.ai)

* [BAND quickstart](https://docs.band.ai/quickstart)
* [BAND MCP setup](https://docs.band.ai/mcp/ai-assistants)
* [Keenable MCP docs](https://docs.keenable.ai/mcp-server)
* [Cognee docs](https://docs.cognee.ai/getting-started/introduction)
* [Cognee sessions and caching](https://docs.cognee.ai/core-concepts/sessions-and-caching)
* [Cognee remember](https://docs.cognee.ai/core-concepts/main-operations/remember)
* [Cognee recall](https://docs.cognee.ai/core-concepts/main-operations/recall)
* [Cognee improve](https://docs.cognee.ai/core-concepts/main-operations/improve)
* [SIE API reference](https://superlinked.com/docs/reference/api)
* [Superlinked cookbook](https://github.com/superlinked/cookbook)
