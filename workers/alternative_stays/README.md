# Alternative-Stays Worker (standalone)

A safe, read-only starter agent for the Plan My Stay project. It works without BAND: JSON request in, JSON result out. A teammate can later connect it through BAND's Python SDK and the shared message envelope.

## Run offline without credentials

Run from the **repository root**:

```bash
python3 -m workers.alternative_stays.worker --input workers/alternative_stays/fixtures/demo_request.json
python3 -m unittest discover -s workers/alternative_stays/tests -v
```

The fixture uses an explicitly **fictional** property. An offline run proves the protocol and logic, **not** any external integration.

## Enable web discovery and page details (Keenable)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r workers/alternative_stays/requirements.txt
python -m workers.alternative_stays.worker --input workers/alternative_stays/fixtures/demo_request.json --search
```

The official `keenable` Python SDK can perform a search without an API key. Optionally set `KEENABLE_API_KEY` for higher limits. The worker excludes Airbnb and Booking.com hostnames, including regional Airbnb domains, and fetches indexed page detail only for allowed results. Both discovery and page details are marked unverified; they do not prove date-specific availability or all-in price.

## Enable real SIE classification (Superlinked)

Set `SIE_API_KEY` for SIE Serverless (default endpoint `https://api.superlinked.com`) or `SIE_BASE_URL` for a compatible SIE server. `SIE_MODEL` defaults to `fastino/GLiNER2.5-Decide`. Run:

```bash
python -m workers.alternative_stays.worker --input workers/alternative_stays/fixtures/demo_request.json --search --sie
```

The classifier returns label scores only, not verified amenities or occupancy. No config is committed; set environment variables in your process.

## Integration contract

Call `handle(request_dict, discover=..., classify=...)` from Python, or run the CLI to print a `stay.results.v1` JSON payload. The worker accepts `stay.search.v1` requests with `request_id`, `destination`, `check_in`, `check_out`, `guests`, optional `max_total_usd`, `required_amenities`, `preferences`, and `provided_listings`. `stay.verify.v1` returns an explicit `unverified` result until a real authorized rate/availability adapter is implemented.

Your teammate's BAND orchestrator should send a `stay.search.v1` message to this worker and collect its JSON response. The BAND integration itself is intentionally not fabricated here.

## Logging and verification boundaries

- Official SDK calls only execute when explicitly enabled with CLI flags. JSON operational logs go to stderr and include allowed/rejected discovered URLs, fetched URLs, SIE classifications, errors, latency, and request usage counts. SIE metering metadata is included when returned by the service.
- Keenable Search and Fetch produce indexed discoveries and page details, not proof of date-specific availability or all-in price.
- The deterministic requirement check is literal text matching over fetched page text. It is a screening signal, not a verified amenity or booking fact. SIE labels are model inferences, not verified amenities or cancellation terms.
- A user-provided price is *user reported*, not a fresh bookable quote. Price and availability defaults remain unknown.
- This worker intentionally does not scrape or automate Airbnb or Booking.com, reserve stays, login to accounts, or submit payments.
- The dated price ceiling and availability cannot be validated with this discovery-only interface. For production ranking, add an authorized inventory API that supplies dated prices, taxes, fees, and inventory before treating those requirements as confirmed.

Official docs: https://docs.keenable.ai/sdks and https://superlinked.com/models/fastino-gliner2-5-decide
