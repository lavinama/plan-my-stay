"""The Concierge's frontier model: `AnthropicLLM` (Messages API over httpx) and `FakeLLM` (scripted, for tests).

Both implement `plan_my_stay.ports.LLMPort`. The key is held in a `Secret`, revealed only to build the
request header, and never put in an exception message or a log record. Prompts can contain meeting
addresses, so they are never logged; only the model name, token counts and status codes are.
"""

import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from plan_my_stay.config import Secret

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# The current API version per platform.claude.com/docs/en/api/versioning (read 14 Nov 2026).
# It removes `temperature`, `top_p` and `top_k`, so none of them is sent.
ANTHROPIC_VERSION = "2026-10-21"  # UNVERIFIED: read from the versioning page; no live call has accepted it yet
DEFAULT_MODEL = "claude-opus-5-5"
MODEL_ENV_VAR = "CONCIERGE_MODEL"

# UNVERIFIED: which statuses are transient (429 rate limit, 5xx, 529 overloaded) is assumed, not confirmed live.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 529})
_MAX_RETRY_DELAY = 10.0
# Keywords structured output rejects with a 400 (docs: structured-outputs, "Schema rules").
_UNSUPPORTED_KEYWORDS = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "maxItems"}
)


class LLMError(RuntimeError):
    """The model call failed. The message names the cause (status, error type) and never a key or a prompt."""


def strict_schema(schema: Any) -> Any:
    """A copy of `schema` that structured output accepts: every object closed (`additionalProperties: false`),
    unsupported constraints dropped. Callers validate the result with pydantic, which enforces the dropped limits."""
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned = {
        key: strict_schema(value)
        for key, value in schema.items()
        if key not in _UNSUPPORTED_KEYWORDS and not (key == "minItems" and value not in (0, 1))
    }
    if cleaned.get("type") == "object" or "properties" in cleaned:
        cleaned["additionalProperties"] = False
    return cleaned


class AnthropicLLM:
    """LLMPort over the Anthropic Messages API.

    UNVERIFIED: written from the vendor docs only. No live call has been made, so the response shape
    (`content[].text`, `stop_reason`, `usage`) and the structured-output request
    (`output_config.format = {type: json_schema, schema}`) are unconfirmed against the real API.
    """

    def __init__(
        self,
        api_key: Secret,
        model: str | None = None,
        http: httpx.Client | None = None,
        *,
        max_tokens: int = 4096,
        max_retries: int = 2,
        timeout: float = 60.0,
        api_version: str = ANTHROPIC_VERSION,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_tokens < 1 or max_retries < 0:
            raise ValueError("max_tokens must be positive and max_retries must not be negative")
        self._api_key = api_key
        self._model = model or (os.environ.get(MODEL_ENV_VAR) or "").strip() or DEFAULT_MODEL
        self._http = http if http is not None else httpx.Client(timeout=timeout)
        self._owns_http = http is None
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._api_version = api_version
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"AnthropicLLM(model={self._model!r})"

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def complete(self, *, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_schema is not None:
            # UNVERIFIED: `output_config.format = {type: json_schema, schema}` is from the structured-outputs docs
            # and has not been exercised against the live API (nor has the model default "claude-opus-5-5").
            body["output_config"] = {"format": {"type": "json_schema", "schema": strict_schema(json_schema)}}
        data = self._post(body)
        text = self._text_of(data)
        if json_schema is not None:
            self._require_json_object(text)
        return text

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key.reveal(),
            "anthropic-version": self._api_version,
            "content-type": "application/json",
        }

    def _post(self, body: Mapping[str, Any]) -> dict[str, Any]:
        response = self._send_with_retries(body)
        if response.status_code >= 400:
            raise LLMError(f"Anthropic API returned {response.status_code} ({_error_type(response)})")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("Anthropic API answered with something that is not JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("Anthropic API answered with an unexpected body")
        usage = data.get("usage")
        counts = usage if isinstance(usage, dict) else {}
        logger.info(
            "llm call model=%s input_tokens=%s output_tokens=%s",
            self._model,
            counts.get("input_tokens"),
            counts.get("output_tokens"),
        )
        return data

    def _send_with_retries(self, body: Mapping[str, Any]) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.post(ANTHROPIC_URL, headers=self._headers(), json=body)
            except httpx.HTTPError as exc:
                raise LLMError(f"request failed: {type(exc).__name__}") from exc
            if response.status_code not in _RETRY_STATUSES or attempt == self._max_retries:
                return response
            delay = _retry_delay(response, attempt)
            logger.warning("llm call got %s, retrying in %.1fs (attempt %d)", response.status_code, delay, attempt + 1)
            self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover - the loop always returns

    @staticmethod
    def _text_of(data: Mapping[str, Any]) -> str:
        # UNVERIFIED: the response fields (`content[].type == "text"`, `stop_reason` values "max_tokens" and
        # "refusal") are assumed from the Messages API docs; the page I could read stopped before the response schema.
        stop_reason = data.get("stop_reason")
        if stop_reason == "max_tokens":
            raise LLMError("the model's answer was cut off (max_tokens)")
        if stop_reason == "refusal":
            raise LLMError("the model refused the request")
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMError("the answer has no content")
        text = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            raise LLMError("the answer has no text block")
        return text

    @staticmethod
    def _require_json_object(text: str) -> None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("structured output is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMError("structured output is not a JSON object")


def _error_type(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
        return str(error.get("type")) if isinstance(error, dict) and error.get("type") else "no error type"
    except (ValueError, AttributeError):
        return "no error type"


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    backoff = 0.5 * 2**attempt
    try:
        asked = float(response.headers.get("retry-after", ""))
    except ValueError:
        asked = 0.0
    return min(max(backoff, asked), _MAX_RETRY_DELAY)


@dataclass(frozen=True)
class LLMCall:
    """One call a `FakeLLM` received."""

    system: str
    prompt: str
    json_schema: dict[str, Any] | None


ScriptStep = str | Exception | Callable[[LLMCall], str]


class FakeLLM:
    """A scripted LLMPort. Each call plays the next step: text to return, an exception to raise, or a function of the call.
    When the script runs out it uses `fallback`, or raises LLMError so a test that over-calls the model fails loudly."""

    def __init__(self, script: Iterable[ScriptStep] = (), *, fallback: ScriptStep | None = None) -> None:
        self._script = list(script)
        self._fallback = fallback
        self._calls: list[LLMCall] = []

    @property
    def calls(self) -> tuple[LLMCall, ...]:
        return tuple(self._calls)

    def complete(self, *, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        call = LLMCall(system=system, prompt=prompt, json_schema=json_schema)
        self._calls.append(call)
        if self._script:
            step: ScriptStep | None = self._script.pop(0)
        else:
            step = self._fallback
        if step is None:
            raise LLMError("FakeLLM script exhausted")
        if isinstance(step, Exception):
            raise step
        return step(call) if callable(step) else step
