"""An in-process fake of the Cognee Cloud calls the memory package makes, served through respx.

It checks what a real tenant would check (both auth headers, the dataset UUID) and records every
request, so tests can assert on request shapes. Retrieval is deliberately noisy: `recall` returns
everything in the session or graph and ignores the query, like a semantic search that surfaces other
travelers' entries. The code under test has to filter by user and city itself.
"""

import datetime as dt
import email
import email.policy
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://tenant.fake.cognee.test"
DATASET_ID = "0b7c1c1e-2f0f-4a43-9d57-3a1a0c9d6e11"
API_KEY = "cog-key-SECRET-123456"
TENANT_ID = "tenant-SECRET-abcdef"

_SESSION_PATH = re.compile(r"^/api/v1/sessions/(?P<id>.+)$")
_DATASET_PATH = re.compile(r"^/api/v1/datasets/(?P<id>[^/]+)$")


@dataclass(frozen=True)
class Recorded:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    json: Any = None
    form: dict[str, str] | None = None
    raw_path: str = ""  # the path as sent, percent-encoded


@dataclass
class _Failure:
    kind: str  # "status", "timeout" or "connect_error"
    status: int = 500
    body: str = ""
    path_contains: str = ""


@dataclass
class FakeCognee:
    dataset_id: str = DATASET_ID
    sessions: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    permanent: list[str] = field(default_factory=list)  # text written straight to permanent memory
    bridged: list[str] = field(default_factory=list)  # session text `improve` bridged into the graph
    requests: list[Recorded] = field(default_factory=list)
    improve_calls: list[dict[str, Any]] = field(default_factory=list)
    list_sessions_shape: str = "list"  # "list", "wrapped" or "ids" (the real shape is unverified)
    _failures: list[_Failure] = field(default_factory=list)
    _bridged: set[tuple[str, str]] = field(default_factory=set)
    _counter: int = 0

    @property
    def graph(self) -> list[str]:
        """Everything the graph holds: permanent writes, then whatever `improve` bridged in."""
        return [*self.permanent, *self.bridged]

    # --- scripting failures -------------------------------------------------------------

    def fail_next(self, status: int = 500, body: str = "boom", *, times: int = 1, on: str = "") -> None:
        self._failures.extend(_Failure("status", status, body, on) for _ in range(times))

    def time_out_next(self, *, times: int = 1, on: str = "") -> None:
        self._failures.extend(_Failure("timeout", path_contains=on) for _ in range(times))

    def connect_error_next(self, *, times: int = 1, on: str = "") -> None:
        self._failures.extend(_Failure("connect_error", path_contains=on) for _ in range(times))

    # --- inspection ---------------------------------------------------------------------

    def calls(self, method: str, path_prefix: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and r.path.startswith(path_prefix)]

    def session_answers(self, session_id: str) -> list[str]:
        return [qa["answer"] for qa in self.sessions.get(session_id, {}).get("qas", [])]

    # --- the server ---------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        recorded = self._record(request)
        failure = self._next_failure(recorded.path)
        if failure is not None:
            return self._fail(failure, request)
        if request.headers.get("x-api-key") != API_KEY or request.headers.get("x-tenant-id") != TENANT_ID:
            sent = request.headers.get("x-api-key", "")
            return httpx.Response(401, json={"detail": f"Invalid credentials (key {sent})"})
        return self._route(recorded)

    def _record(self, request: httpx.Request) -> Recorded:
        content_type = request.headers.get("content-type", "")
        body_json, form = None, None
        if content_type.startswith("application/json") and request.content:
            body_json = json.loads(request.content)
        elif content_type.startswith("multipart/form-data"):
            form = _parse_multipart(content_type, request.content)
        recorded = Recorded(
            method=request.method,
            path=request.url.path,
            query=request.url.query.decode(),
            headers={k.lower(): v for k, v in request.headers.items()},
            json=body_json,
            form=form,
            raw_path=request.url.raw_path.split(b"?")[0].decode(),
        )
        self.requests.append(recorded)
        return recorded

    def _next_failure(self, path: str) -> _Failure | None:
        for index, failure in enumerate(self._failures):
            if failure.path_contains in path:
                return self._failures.pop(index)
        return None

    def _fail(self, failure: _Failure, request: httpx.Request) -> httpx.Response:
        if failure.kind == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure.kind == "connect_error":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(failure.status, text=failure.body)

    def _route(self, req: Recorded) -> httpx.Response:
        match (req.method, req.path):
            case ("POST", "/api/v1/remember/entry"):
                return self._remember_entry(req)
            case ("POST", "/api/v1/remember"):
                return self._remember_text(req)
            case ("POST", "/api/v1/recall"):
                return self._recall(req)
            case ("POST", "/api/v1/improve"):
                return self._improve(req)
            case ("GET", "/api/v1/sessions"):
                return self._list_sessions()
            case ("POST", "/api/v1/datasets/"):
                return httpx.Response(200, json={"id": self.dataset_id, "name": (req.json or {}).get("name")})
        if (m := _SESSION_PATH.match(req.path)) is not None:
            return self._session(req.method, m.group("id"))
        if (m := _DATASET_PATH.match(req.path)) is not None and req.method == "DELETE":
            return httpx.Response(200) if m.group("id") == self.dataset_id else httpx.Response(404, json={"detail": "no"})
        return httpx.Response(404, json={"detail": f"no route {req.method} {req.path}"})

    def _wrong_dataset(self, given: Any) -> httpx.Response | None:
        if given != self.dataset_id:
            return httpx.Response(404, json={"detail": "Dataset not found or no access"})
        return None

    def _next_id(self) -> str:
        self._counter += 1
        return f"qa-{self._counter}"

    def _add_qa(self, session_id: str, question: str, answer: str) -> str:
        qa_id = self._next_id()
        session = self.sessions.setdefault(session_id, {"qas": [], "traces": []})
        stamp = dt.datetime(2026, 11, 14, 16, 0, tzinfo=dt.UTC).isoformat()
        session["qas"].append({"qa_id": qa_id, "question": question, "answer": answer, "context": "", "time": stamp})
        return qa_id

    def _remember_entry(self, req: Recorded) -> httpx.Response:
        body = req.json or {}
        if (bad := self._wrong_dataset(body.get("dataset_id"))) is not None:
            return bad
        entry, session_id = body.get("entry") or {}, body.get("session_id")
        if not session_id:
            return httpx.Response(422, json={"detail": "session_id is required"})
        if entry.get("type") == "qa":
            entry_id = self._add_qa(session_id, entry["question"], entry["answer"])
        elif entry.get("type") == "trace":
            entry_id = self._next_id()
            self.sessions.setdefault(session_id, {"qas": [], "traces": []})["traces"].append({"trace_id": entry_id, **entry})
        else:
            return httpx.Response(422, json={"detail": "unknown entry type"})
        return httpx.Response(200, json={"status": "session_stored", "entry_type": entry["type"], "entry_id": entry_id})

    def _remember_text(self, req: Recorded) -> httpx.Response:
        form = req.form or {}
        if (bad := self._wrong_dataset(form.get("datasetId"))) is not None:
            return bad
        if "raw_data" not in form:
            return httpx.Response(422, json={"detail": "raw_data is required"})
        if session_id := form.get("session_id"):
            self._add_qa(session_id, "", form["raw_data"])
            return httpx.Response(200, json={"status": "session_stored", "session_id": session_id})
        self.permanent.append(form["raw_data"])
        return httpx.Response(200, json={"status": "completed", "dataset_id": self.dataset_id})

    def _recall(self, req: Recorded) -> httpx.Response:
        body = req.json or {}
        if (bad := self._wrong_dataset((body.get("datasetIds") or [None])[0])) is not None:
            return bad
        session_id, scope = body.get("sessionId"), body.get("scope")
        scope = ("session" if session_id else "graph") if scope in (None, "auto") else scope
        if scope == "session":
            qas = self.sessions.get(session_id or "", {}).get("qas", [])
            return httpx.Response(200, json=[{"source": "session", "time": q["time"], **q} for q in qas])
        items = [{"source": "graph", "kind": "chunk", "search_type": body.get("searchType"), "text": t} for t in self.graph]
        return httpx.Response(200, json=items)

    def _improve(self, req: Recorded) -> httpx.Response:
        body = req.json or {}
        if (bad := self._wrong_dataset(body.get("datasetId"))) is not None:
            return bad
        self.improve_calls.append(body)
        session_ids = body.get("sessionIds") or []
        for session_id in session_ids:
            for answer in self.session_answers(session_id):
                if (session_id, answer) not in self._bridged:
                    self._bridged.add((session_id, answer))
                    self.bridged.append(answer)
        return httpx.Response(200, json={"status": "completed", "dataset_id": self.dataset_id, "session_ids": session_ids})

    def _list_sessions(self) -> httpx.Response:
        ids = list(self.sessions)
        if self.list_sessions_shape == "wrapped":
            return httpx.Response(200, json={"sessions": [{"session_id": i} for i in ids], "total": len(ids)})
        if self.list_sessions_shape == "ids":
            return httpx.Response(200, json=ids)
        return httpx.Response(200, json=[{"session_id": i, "label": None} for i in ids])

    def _session(self, method: str, session_id: str) -> httpx.Response:
        session = self.sessions.get(session_id)
        if session is None:
            return httpx.Response(404, json={"detail": "session not found"})
        if method == "DELETE":
            del self.sessions[session_id]
            return httpx.Response(200, json={"session_id": session_id, "deleted": True})
        qas, traces = session["qas"], session["traces"]
        detail = {"session_id": session_id, "msg_count": len(qas) + len(traces), "qas": qas[-20:], "traces": traces[-20:]}
        return httpx.Response(200, json=detail)


def _parse_multipart(content_type: str, body: bytes) -> dict[str, str]:
    raw = b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    message = email.message_from_bytes(raw, policy=email.policy.HTTP)
    fields: dict[str, str] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        fields[str(name)] = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
    return fields
