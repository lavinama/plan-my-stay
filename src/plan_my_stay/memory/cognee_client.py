"""A thin httpx wrapper around the Cognee Cloud calls the memory package makes.

Request shapes come from docs/PLAN.md ("Cognee request shapes", tested on 14 Nov) and from the Cognee
API reference. The plan's shapes address a dataset by name; a shared dataset must be addressed by UUID,
so every endpoint's dataset parameter is built in one place, `CogneeClient._dataset_ref`.

Failures raise `CogneeError` (status and a short body excerpt, never headers or keys). A 5xx or a
timeout is retried once after `retry_delay_s`; anything else fails at once.
"""

import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Literal
from urllib.parse import quote

import httpx

from plan_my_stay.config import Secret

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
# `improve` takes 20 to 80 s (docs/PLAN.md); a permanent `remember` ingests and builds the graph before it answers.
SLOW_TIMEOUT_S = 180.0

# UNVERIFIED: recall's `searchType` value for "give me the stored text back verbatim". We parse our own
# `agent=...` lines out of recall results, so a completion (an LLM's paraphrase) would lose them.
GRAPH_SEARCH_TYPE = "CHUNKS"

_EXCERPT_CHARS = 200
_MIN_SCRUBBED_SECRET_CHARS = 4

DatasetEndpoint = Literal["remember_entry", "remember", "recall", "improve"]


class CogneeError(RuntimeError):
    """A Cognee call failed. `status` is the HTTP status (None for a timeout or network error)."""

    def __init__(self, message: str, *, status: int | None = None, body_excerpt: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body_excerpt = body_excerpt


class CogneeClient:
    def __init__(
        self,
        base_url: str,
        api_key: Secret,
        tenant_id: Secret,
        dataset_id: str,
        http: httpx.Client | None = None,
        retry_delay_s: float = 1.0,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        self._base_url = base_url.rstrip("/")
        self._api_key = _checked_secret("api_key", api_key)
        self._tenant_id = _checked_secret("tenant_id", tenant_id)
        self._dataset_id = _checked_uuid(dataset_id, "dataset_id")
        self._retry_delay_s = retry_delay_s
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=DEFAULT_TIMEOUT_S)

    def __repr__(self) -> str:
        return f"CogneeClient(base_url={self._base_url!r}, dataset_id={self._dataset_id!r})"

    def close(self) -> None:
        """Close the HTTP client this object made. An injected client stays open: its owner closes it."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "CogneeClient":
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    # --- the one place that names the dataset parameter ------------------------------------

    def _dataset_ref(self, endpoint: DatasetEndpoint) -> dict[str, Any]:
        """How `endpoint` takes the dataset, always by UUID (a name would create your own, unshared dataset).

        UNVERIFIED: these names are from the Cognee docs (docs.cognee.ai, read 14 Nov), not exercised against our
        tenant. Docs: typed entries take JSON `dataset_id`; plain-text `remember` takes the multipart field
        `datasetId`; `recall` takes `datasetIds` (a list); `improve` takes `datasetId`. If a call answers 404
        or 422 about the dataset, correct it here.
        """
        match endpoint:
            case "remember_entry":
                return {"dataset_id": self._dataset_id}
            case "remember":
                return {"datasetId": self._dataset_id}
            case "recall":
                return {"datasetIds": [self._dataset_id]}
            case "improve":
                return {"datasetId": self._dataset_id}
        raise ValueError(f"unknown Cognee endpoint {endpoint!r}")

    # --- the calls --------------------------------------------------------------------------

    def remember_entry(self, *, session_id: str, entry: Mapping[str, Any]) -> dict[str, Any]:
        """`POST /api/v1/remember/entry`: one typed entry (qa, trace or feedback) in a session."""
        body = {**self._dataset_ref("remember_entry"), "session_id": _required(session_id, "session_id"), "entry": dict(entry)}
        return self._object("POST", "/api/v1/remember/entry", json=body)

    def remember_text(self, text: str, *, session_id: str | None = None, self_improvement: bool = False) -> dict[str, Any]:
        """`POST /api/v1/remember` (multipart): plain text. With a session it goes to that session's cache; without
        one it is permanent graph memory, which ingests and builds the graph before answering (slow)."""
        fields: dict[str, str] = {
            **{k: str(v) for k, v in self._dataset_ref("remember").items()},
            "raw_data": text,
            "self_improvement": "true" if self_improvement else "false",
        }
        if session_id is not None:
            fields["session_id"] = _required(session_id, "session_id")
        # UNVERIFIED: the multipart field names (`datasetId`, `session_id`, `raw_data`, `self_improvement`) come from the
        # docs and the plan, and whether a permanent write (no session) answers only after the graph is built.
        # `files` with no filename is how httpx sends text fields as multipart/form-data (`data=` would be urlencoded).
        files = {name: (None, value) for name, value in fields.items()}
        timeout = DEFAULT_TIMEOUT_S if session_id is not None else SLOW_TIMEOUT_S
        return self._object("POST", "/api/v1/remember", files=files, timeout=timeout)

    def get_session(self, session_id: str) -> dict[str, Any]:
        """`GET /api/v1/sessions/{id}`: the session with its last 20 QA and 20 trace entries."""
        return self._object("GET", f"/api/v1/sessions/{quote(_required(session_id, 'session_id'), safe='')}")

    def list_sessions(self) -> Any:
        """`GET /api/v1/sessions?range=all`. UNVERIFIED: the response shape; use `session_ids_from`."""
        return self._call("GET", "/api/v1/sessions", params={"range": "all"})

    def recall(
        self,
        query: str,
        *,
        session_id: str | None = None,
        scope: str | None = None,
        search_type: str | None = None,
        top_k: int | None = None,
    ) -> list[Any]:
        """`POST /api/v1/recall`: result items, each tagged with its `source` ("session", "graph", "trace", ...)."""
        body: dict[str, Any] = {"query": query, **self._dataset_ref("recall")}
        optional = {"sessionId": session_id, "scope": scope, "searchType": search_type, "topK": top_k}
        body.update({name: value for name, value in optional.items() if value is not None})
        data = self._call("POST", "/api/v1/recall", json=body)
        if not isinstance(data, list):
            raise CogneeError("Cognee POST /api/v1/recall answered with something other than a list")
        return data

    def improve(self, *, session_ids: Sequence[str]) -> dict[str, Any]:
        """`POST /api/v1/improve`: bridge sessions into the permanent graph. Takes 20 to 80 s, so it is never
        retried (a retry would only queue behind the first run)."""
        body = {**self._dataset_ref("improve"), "sessionIds": list(session_ids)}
        return self._object("POST", "/api/v1/improve", json=body, timeout=SLOW_TIMEOUT_S, retries=0)

    def create_dataset(self, name: str) -> dict[str, Any]:
        """`POST /api/v1/datasets/`: creates a dataset (or returns the existing one with that name); `id` is its UUID."""
        return self._object("POST", "/api/v1/datasets/", json={"name": _required(name, "name")})

    def delete_session(self, session_id: str) -> dict[str, Any]:
        """`DELETE /api/v1/sessions/{id}`."""
        return self._object("DELETE", f"/api/v1/sessions/{quote(_required(session_id, 'session_id'), safe='')}")

    def delete_dataset(self, dataset_id: str) -> dict[str, Any]:
        """`DELETE /api/v1/datasets/{dataset_id}`: permanent. The id is explicit on purpose; there is no default."""
        return self._object("DELETE", f"/api/v1/datasets/{_checked_uuid(dataset_id, 'dataset_id')}")

    # --- transport --------------------------------------------------------------------------

    def _object(self, method: str, path: str, **options: Any) -> dict[str, Any]:
        data = self._call(method, path, **options)
        if not isinstance(data, dict):
            raise CogneeError(f"Cognee {method} {path} answered with something other than a JSON object")
        return data

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        retries: int = 1,
    ) -> Any:
        headers = {"X-Api-Key": self._api_key.reveal(), "X-Tenant-Id": self._tenant_id.reveal()}
        if json is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(retries + 1):
            final = attempt == retries
            try:
                response = self._http.request(
                    method, self._base_url + path, headers=headers, json=json, files=files, params=params, timeout=timeout
                )
            except httpx.TimeoutException as exc:
                if not final:
                    self._pause(method, path, "timed out")
                    continue
                raise CogneeError(self._scrub(f"Cognee {method} {path} timed out")) from exc
            except httpx.HTTPError as exc:
                message = self._scrub(f"Cognee {method} {path} failed: {type(exc).__name__}: {exc}")
                raise CogneeError(message) from exc
            if response.status_code >= 500 and not final:
                self._pause(method, path, f"HTTP {response.status_code}")
                continue
            if response.status_code >= 400:
                excerpt = self._excerpt(response.text)
                message = self._scrub(f"Cognee {method} {path} returned HTTP {response.status_code}: {excerpt}")
                raise CogneeError(message, status=response.status_code, body_excerpt=excerpt)
            logger.debug("Cognee %s %s -> %s", method, path, response.status_code)
            return self._decode(response, method, path)
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _pause(self, method: str, path: str, why: str) -> None:
        logger.warning("Cognee %s %s: %s; retrying once", method, path, why)
        time.sleep(self._retry_delay_s)

    def _decode(self, response: httpx.Response, method: str, path: str) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise CogneeError(
                f"Cognee {method} {path} answered HTTP {response.status_code} but the body is not JSON",
                status=response.status_code,
                body_excerpt=self._excerpt(response.text),
            ) from None

    def _excerpt(self, body: str) -> str:
        return self._scrub(" ".join(body[:2000].split()))[:_EXCERPT_CHARS]

    def _scrub(self, text: str) -> str:
        """Replace any secret the server (or a library) echoed back. Applied to every message we raise or log."""
        for secret in (self._api_key, self._tenant_id):
            value = secret.reveal()
            if len(value) >= _MIN_SCRUBBED_SECRET_CHARS:
                text = text.replace(value, "***")
        return text


def session_ids_from(payload: Any) -> tuple[str, ...]:
    """Session ids out of a `list_sessions` answer. UNVERIFIED shape, so this accepts a list of ids, a list of
    objects with `session_id` / `sessionId` / `id`, or an object holding such a list under `sessions`,
    `items`, `data` or `results`. Anything else gives no ids."""
    if isinstance(payload, Mapping):
        payload = next((payload[k] for k in ("sessions", "items", "data", "results") if isinstance(payload.get(k), list)), [])
    if not isinstance(payload, list):
        return ()
    ids: list[str] = []
    for item in payload:
        value = item if isinstance(item, str) else next(
            (item[k] for k in ("session_id", "sessionId", "id") if isinstance(item, Mapping) and isinstance(item.get(k), str)), None
        )
        if isinstance(value, str) and value and value not in ids:
            ids.append(value)
    return tuple(ids)


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _checked_secret(name: str, secret: Secret) -> Secret:
    if not isinstance(secret, Secret):
        raise TypeError(f"{name} must be wrapped in plan_my_stay.config.Secret")
    if not secret.reveal().strip():
        raise ValueError(f"{name} is empty")
    return secret


def _checked_uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{name} must be the dataset's UUID, not its name") from exc
