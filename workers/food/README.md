# Adaptive Food Worker

The food worker replans meal options when the coordinator publishes a new trip
revision. It retains candidate identity and menu evidence, recalculates
geographic fit against the new stay or activity anchor, and flags derived data
that must be refreshed. Its request contract is `food.replan.v1`; its result is
`food.results.v1`.

## Request boundary

The coordinator sends the authoritative `TripState`, meal slots, traveler
profiles, candidate pool, and (when applicable) the previous trip revision and
`trip.context_changed.v1` event. Every candidate fact should include provenance
and an observation timestamp. Explicit traveler feedback belongs in the
traveler profile or a separately stored feedback event; a recommendation alone
must not update a permanent preference.

The worker can deterministically validate structured hard requirements and
rank soft cuisine preferences. It fails closed when required attributes are
unknown. Its geographic calculation is straight-line distance only. A route
adapter must supply a travel estimate tagged with the same trip revision before
the result can report a current travel time.

## Run locally

```sh
python3 workers/food/worker.py < workers/food/fixtures/replan_after_stay_change.json
```

## Data source and model adapters

This package consumes a candidate pool; it does not itself query Google Places,
Keenable, Cognee, or SIE. The intended pipeline is:

1. Discovery adapters (for example Google Places and Keenable) create/update the
   candidate pool with source URLs, place IDs, timestamps, and field-level
   provenance.
2. Detail adapters fetch permitted menu and hours pages for shortlisted
   candidates. Reuse those facts across trip revisions while their freshness
   policy allows it.
3. Cognee may retrieve confirmed traveler feedback and previously verified
   evidence. It must not turn an unconfirmed recommendation into a preference.
4. SIE models may extract normalized menu attributes, encode profile/candidate
   text, or rerank a bounded shortlist. Model-derived attributes remain
   unverified until grounded in source evidence; hard requirements are checked
   deterministically here.
5. The coordinator combines options across meal slots with activities, travel
   time, and remaining trip budget. This worker does not make reservations or
   claim live availability.

## Freshness and limits

Menu and hours freshness windows are configurable in
`trip.freshness_policy`. Results keep the source URLs and observed timestamps.
Opening hours are not reservation availability; menu prices are not a complete
meal total; and straight-line distance is not travel time. Search and enrichment
should be bounded by the coordinator, then expanded only when a slot has too
few viable options.

## Coordinator integration still needed

The repository does not contain BAND's orchestration code. BAND should dispatch
`food.replan.v1` after initial trip setup and after relevant context-change
events, then reject or re-evaluate results whose `trip_revision` is stale. The
Google, Keenable, Cognee, and SIE adapters and credentials are intentionally
outside this worker contract so they can be integrated without taking
ownership of the teammate's orchestration implementation.
