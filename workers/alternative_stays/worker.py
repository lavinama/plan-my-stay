"""Read-only alternative stays discovery and classification worker.

BAND integration boundary: handle(dict) -> dict. No booking, login, payment,
or automated Airbnb access is supported. Web discovery is not a live quote.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

WORKER_NAME = "alternative-stays"
DEFAULT_SIE_BASE_URL = "https://api.superlinked.com"
BLOCKED_DISCOVERY_HOSTS = ("airbnb.com", "booking.com")
CLASS_LABELS = [
    "entire apartment or vacation home",
    "serviced apartment or aparthotel",
    "private room in a shared property",
    "kitchen mentioned",
    "wireless internet mentioned",
    "dedicated workspace mentioned",
    "flexible or free cancellation mentioned",
    "non-refundable booking mentioned",
    "additional cleaning or service fees mentioned",
]

logger = logging.getLogger(WORKER_NAME)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_blocked(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return True
    labels = host.split(".")
    return (
        any(host == item or host.endswith("." + item) for item in BLOCKED_DISCOVERY_HOSTS)
        or any(label in {"airbnb", "booking"} for label in labels)
    )


def _text(value: Any, limit: int = 1500) -> str:
    return str(value or "")[:limit]


def validate(request: dict[str, Any]) -> None:
    if request.get("type") not in ("stay.search.v1", "stay.verify.v1"):
        raise ValueError("Expected type stay.search.v1 or stay.verify.v1")
    if not isinstance(request.get("request_id"), str) or not request["request_id"].strip():
        raise ValueError("request_id must be a nonempty string")
    if request["type"] == "stay.verify.v1":
        return
    if not isinstance(request.get("destination"), str) or not request["destination"].strip():
        raise ValueError("destination is required")
    try:
        check_in = date.fromisoformat(request["check_in"])
        check_out = date.fromisoformat(request["check_out"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("check_in and check_out must be ISO dates") from exc
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")
    guests = request.get("guests")
    if type(guests) is not int or guests < 1:
        raise ValueError("guests must be a positive integer")
    max_total = request.get("max_total_usd")
    if max_total is not None and (type(max_total) not in (float, int) or max_total <= 0):
        raise ValueError("max_total_usd must be positive or null")
    for key in ("required_amenities", "preferences", "provided_listings"):
        if not isinstance(request.get(key, []), list):
            raise ValueError(f"{key} must be an array")


def _plain(value: Any) -> Any:
    """Make an SDK response safe for JSON without assuming its object type."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if hasattr(value, "__dict__"):
        return {k: _plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _log(event: str, **fields: Any) -> None:
    """Emit machine-readable operational logs without credentials or page bodies."""
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


def _safe_error(exc: Exception) -> dict[str, Any]:
    text = f"{type(exc).__name__}: {exc}"
    for name in ("KEENABLE_API_KEY", "SIE_API_KEY"):
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(authorization|api[_-]?key)(\s*[:=]\s*)[^\s,;]+", r"\1\2[REDACTED]", text)
    status = getattr(exc, "status_code", None) or getattr(exc, "statusCode", None)
    return {"type": type(exc).__name__, "status_code": status, "message": text[:500]}


def keenable_discover(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Keenable indexed discovery only, deliberately excluding Airbnb/Booking.com."""
    from keenable import Keenable  # installed only when live search is enabled

    prefs = " ".join(_text(x, 60) for x in request.get("required_amenities", [])[:8])
    query = (
        f"independent serviced apartments aparthotels vacation rentals in "
        f"{request['destination']} {prefs} official property accommodation sites"
    )
    client = Keenable(client_source="PlanMyStay-AlternativeStays")
    result = client.search(
        query,
        max_results=8,
        exclude_domains=list(BLOCKED_DISCOVERY_HOSTS),
        disable_cards=True,
        snippet_max_length=1200,
    )
    candidates: list[dict[str, Any]] = []
    for hit in result:
        url = _text(getattr(hit, "url", ""), 2000)
        if not url.startswith("https://") or _is_blocked(url):
            _log("keenable.discovery.rejected", url=url, reason="blocked_host_or_non_https")
            continue
        candidates.append({
            "name": _text(getattr(hit, "title", ""), 180),
            "source_url": url,
            "description": _text(getattr(hit, "snippet", ""), 1200),
            "source_type": "keenable_indexed_discovery",
            "indexed_at": _plain(getattr(hit, "acquired_at", None)),
        })
        _log("keenable.discovery", url=url, title=_text(getattr(hit, "title", ""), 180))
    _log("keenable.search.complete", results=len(candidates), indexed_discovery=True)
    return candidates


def keenable_fetch(url: str) -> dict[str, Any]:
    """Fetch indexed page detail; it is enrichment, not live booking verification."""
    from keenable import Keenable

    if not url.startswith("https://") or _is_blocked(url):
        raise ValueError("Fetch URL is not permitted")
    client = Keenable(client_source="PlanMyStay-AlternativeStays")
    page = client.fetch(url, max_chars=5000)
    _log("keenable.fetch.complete", url=url, chars=len(_text(getattr(page, "content", ""), 5000)), indexed_detail=True)
    return {
        "title": _text(getattr(page, "title", ""), 180),
        "content": _text(getattr(page, "content", ""), 5000),
        "source_type": "keenable_fetch_indexed_detail",
        "fetched_at": timestamp(),
    }


def sie_classify(text: str) -> list[Any]:
    """Return raw Superlinked label scores; never promote them to verified facts."""
    from sie_sdk import SIEClient
    from sie_sdk.types import Item

    if not (os.getenv("SIE_API_KEY") or os.getenv("SIE_BASE_URL")):
        raise RuntimeError("Configure SIE_API_KEY or SIE_BASE_URL before --sie")
    client = SIEClient(
        os.getenv("SIE_BASE_URL", DEFAULT_SIE_BASE_URL),
        api_key=os.getenv("SIE_API_KEY"),
    )
    short = " ".join(text.split()[:240])  # model context is limited
    if not short:
        return []
    result = client.extract(
        os.getenv("SIE_MODEL", "fastino/GLiNER2.5-Decide"),
        Item(text=short),
        labels=CLASS_LABELS,
    )
    plain = _plain(result)
    if isinstance(plain, dict):
        data = plain.get("classifications", plain.get("entities", []))
        request_meta = plain.get("request", {})
        _log(
            "sie.classification",
            model=os.getenv("SIE_MODEL", "fastino/GLiNER2.5-Decide"),
            classifications=data,
            usage=request_meta.get("usage") if isinstance(request_meta, dict) else None,
            credits_debited=request_meta.get("credits_debited") if isinstance(request_meta, dict) else None,
        )
        return data
    _log("sie.classification", model=os.getenv("SIE_MODEL", "fastino/GLiNER2.5-Decide"), classifications=plain)
    return plain if isinstance(plain, list) else []


def _validate_hard_requirements(
    request: dict[str, Any], evidence_text: str, *, evidence_source: str
) -> dict[str, Any]:
    """Deterministically check literal amenity mentions; never infer booking facts."""
    normalized = " ".join(evidence_text.casefold().split())
    mention_status = f"mentioned_in_{evidence_source}"
    checks = []
    for amenity in request.get("required_amenities", []):
        needle = " ".join(str(amenity).casefold().split())
        matched = bool(needle and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized))
        checks.append({
            "requirement": str(amenity),
            "status": mention_status if matched else f"not_confirmed_in_{evidence_source}",
        })
    return {
        "status": "all_required_amenities_mentioned" if all(x["status"] == mention_status for x in checks) else "requirements_unconfirmed",
        "checks": checks,
        "availability": "unknown",
        "dated_all_in_price": "unknown",
        "validation_scope": f"literal text matching over {evidence_source}; not live availability or pricing verification",
    }


def _offer(request: dict[str, Any], candidate: dict[str, Any], ordinal: int) -> dict[str, Any]:
    source_type = candidate.get("source_type", "user_provided")
    price = candidate.get("total_usd") if source_type == "user_provided" else None
    if type(price) not in (int, float) or price <= 0:
        price = None
    description = _text(candidate.get("description", ""), 1500)
    url = _text(candidate.get("source_url", ""), 2000) or None
    return {
        "offer_id": f"{request['request_id']}:alternative:{ordinal}",
        "property_name": _text(candidate.get("name", "Unknown property"), 180),
        "provider": source_type,
        "url": url,
        "requested_stay": {
            "check_in": request["check_in"],
            "check_out": request["check_out"],
            "guests": request["guests"],
        },
        "price": {
            "total_usd": price,
            "status": "user_reported_unverified" if price is not None else "unknown",
            "fees_complete": False,
        },
        "availability": "unknown",
        "hard_constraints": "not_evaluated_missing_verified_data",
        "signals": {"sie_labels": [], "status": "not_run"},
        "evidence": [{
            "source_url": url,
            "excerpt": description,
            "source_type": source_type,
            "indexed_at": candidate.get("indexed_at"),
            "collected_at": timestamp(),
            "verified": False,
        }],
        "unresolved": ["actual availability for requested dates", "dated all-in price including taxes and fees", "confirmation of mandatory amenities"],
        "status": "requires_source_verification",
    }


def handle(
    request: dict[str, Any],
    *,
    discover: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    classify: Callable[[str], list[Any]] | None = None,
    fetch: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stable BAND-facing JSON-in/JSON-out interface, independent of BAND SDK."""
    validate(request)
    if request["type"] == "stay.verify.v1":
        return {
            "type": "stay.verify.result.v1",
            "request_id": request["request_id"],
            "agent_id": WORKER_NAME,
            "status": "unverified",
            "reason": "No authorized dated-inventory provider is configured for this offer",
        }

    start = time.perf_counter()
    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    for item in request.get("provided_listings", [])[:25]:
        if isinstance(item, dict):
            candidates.append({**item, "source_type": "user_provided"})

    search_count = 0
    fetch_count = 0
    fetch_errors = 0
    sie_errors = 0
    fetch_attempts = 0
    sie_attempts = 0
    api_errors: list[dict[str, Any]] = []
    latencies: dict[str, list[int]] = {"search_ms": [], "fetch_ms": [], "sie_ms": []}
    usage: dict[str, Any] = {"keenable_search_requests": 0, "keenable_fetch_requests": 0, "sie_requests": 0}
    if discover is not None:
        op_start = time.perf_counter()
        try:
            discovered = discover(request)
            candidates.extend(discovered)
            search_count = 1
            usage["keenable_search_requests"] = 1
        except Exception as exc:
            err = _safe_error(exc)
            api_errors.append({"service": "keenable_search", **err})
            warnings.append(f"Discovery unavailable: {err['message']}")
            _log("keenable.error", operation="search", **err)
        finally:
            elapsed_ms = round((time.perf_counter() - op_start) * 1000)
            latencies["search_ms"].append(elapsed_ms)
            _log("keenable.search.metrics", elapsed_ms=elapsed_ms, requests=search_count)

    seen: set[str] = set()
    offers: list[dict[str, Any]] = []
    sie_calls = 0
    for candidate in candidates:
        key = candidate.get("source_url") or f"manual:{candidate.get('name', '')}"
        if key in seen:
            continue
        seen.add(key)
        offer = _offer(request, candidate, len(offers) + 1)
        excerpt = offer["evidence"][0]["excerpt"]
        enrichment_text = excerpt
        evidence_source = "user_provided_text" if candidate.get("source_type") == "user_provided" else "keenable_search_snippet"
        if fetch is not None and offer["url"] and candidate.get("source_type") == "keenable_indexed_discovery":
            op_start = time.perf_counter()
            fetch_attempts += 1
            usage["keenable_fetch_requests"] = fetch_attempts
            try:
                page = fetch(offer["url"])
                fetch_count += 1
                detail = _text(page.get("content", ""), 5000)
                enrichment_text = detail or excerpt
                if detail:
                    evidence_source = "keenable_indexed_page_details"
                offer["page_details"] = {
                    "title": _text(page.get("title", ""), 180),
                    "content_excerpt": detail[:1200],
                    "source_type": page.get("source_type", "keenable_fetch_indexed_detail"),
                    "fetched_at": page.get("fetched_at", timestamp()),
                    "verified_live": False,
                }
                offer["evidence"].append({
                    "source_url": offer["url"],
                    "excerpt": detail[:1200],
                    "source_type": page.get("source_type", "keenable_fetch_indexed_detail"),
                    "collected_at": page.get("fetched_at", timestamp()),
                    "verified": False,
                })
            except Exception as exc:
                fetch_errors += 1
                err = _safe_error(exc)
                api_errors.append({"service": "keenable_fetch", "url": offer["url"], **err})
                warnings.append(f"Page enrichment unavailable: {err['message']}")
                _log("keenable.error", operation="fetch", url=offer["url"], **err)
            finally:
                elapsed_ms = round((time.perf_counter() - op_start) * 1000)
                latencies["fetch_ms"].append(elapsed_ms)
                _log("keenable.fetch.metrics", url=offer["url"], elapsed_ms=elapsed_ms, requests=fetch_attempts)
        offer["hard_constraints"] = _validate_hard_requirements(
            request, enrichment_text, evidence_source=evidence_source
        )
        if classify is not None and offer["evidence"][0]["excerpt"]:
            op_start = time.perf_counter()
            sie_attempts += 1
            usage["sie_requests"] = sie_attempts
            try:
                offer["signals"] = {
                    "sie_labels": classify(enrichment_text),
                    "status": "model_inference_not_verified",
                }
                sie_calls += 1
            except Exception as exc:
                sie_errors += 1
                err = _safe_error(exc)
                api_errors.append({"service": "superlinked_sie", "offer_id": offer["offer_id"], **err})
                warnings.append(f"SIE unavailable for {offer['offer_id']}: {err['message']}")
                _log("sie.error", offer_id=offer["offer_id"], **err)
                offer["signals"]["status"] = "error"
            finally:
                elapsed_ms = round((time.perf_counter() - op_start) * 1000)
                latencies["sie_ms"].append(elapsed_ms)
                _log("sie.metrics", offer_id=offer["offer_id"], elapsed_ms=elapsed_ms, requests=sie_attempts)
        offers.append(offer)

    return {
        "type": "stay.results.v1",
        "request_id": request["request_id"],
        "agent_id": WORKER_NAME,
        "created_at": timestamp(),
        "offers": offers,
        "meta": {
            "search_calls": search_count,
            "fetch_calls": fetch_count,
            "fetch_errors": fetch_errors,
            "sie_calls": sie_calls,
            "sie_errors": sie_errors,
            "elapsed_ms": round((time.perf_counter() - start) * 1000),
            "latencies_ms": {k: v for k, v in latencies.items()},
            "usage": usage,
            "api_errors": api_errors,
            "all_offers_require_live_verification": True,
            "warnings": warnings,
        },
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    parser = argparse.ArgumentParser(description="Standalone alternative stays worker")
    parser.add_argument("--input", required=True, help="Stay request JSON file, or '-' for stdin")
    parser.add_argument("--search", action="store_true", help="Call real Keenable Search (keyless supported)")
    parser.add_argument("--sie", action="store_true", help="Call Superlinked SIE (requires credentials/runtime)")
    args = parser.parse_args()
    try:
        if args.input == "-":
            request = json.load(sys.stdin)
        else:
            with open(args.input, encoding="utf-8") as f:
                request = json.load(f)
        result = handle(
            request,
            discover=keenable_discover if args.search else None,
            classify=sie_classify if args.sie else None,
            fetch=keenable_fetch if args.search else None,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        if (args.search and result["meta"]["search_calls"] == 0) or (
            args.sie and result["offers"] and result["meta"]["sie_calls"] == 0
        ):
            return 1  # explicitly signal unavailable requested integration
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Invalid request: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
