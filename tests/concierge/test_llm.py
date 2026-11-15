"""AnthropicLLM (httpx, mocked with respx; no network, no real key) and FakeLLM (scripted)."""

import inspect
import json
import logging

import httpx
import pytest
import respx

from plan_my_stay.concierge.llm import ANTHROPIC_URL, DEFAULT_MODEL, AnthropicLLM, FakeLLM, LLMCall, LLMError
from plan_my_stay.config import Secret
from plan_my_stay.ports import LLMPort

KEY = "sk-ant-test-0123456789"


def _ok(text: str = "hello", **extra) -> httpx.Response:
    return httpx.Response(200, json={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", **extra})


def _llm(**kwargs) -> AnthropicLLM:
    return AnthropicLLM(Secret(KEY), sleep=lambda _s: None, **kwargs)


def test_both_implement_the_llm_port():
    expected = inspect.signature(LLMPort.complete)
    for implementation in (AnthropicLLM.complete, FakeLLM.complete):
        assert inspect.signature(implementation) == expected


@respx.mock
def test_complete_posts_the_messages_api_request_and_returns_the_text():
    route = respx.post(ANTHROPIC_URL).mock(return_value=_ok("Your itinerary."))

    text = _llm().complete(system="You write itineraries.", prompt="Facts: ...")

    assert text == "Your itinerary."
    request = route.calls.last.request
    assert request.headers["x-api-key"] == KEY
    assert request.headers["anthropic-version"]
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body["model"] == DEFAULT_MODEL == "claude-opus-5-5"
    assert body["system"] == "You write itineraries."
    assert body["messages"] == [{"role": "user", "content": "Facts: ..."}]
    assert body["max_tokens"] > 0
    assert "output_config" not in body and "temperature" not in body


@respx.mock
def test_the_model_comes_from_the_environment_unless_given(monkeypatch):
    route = respx.post(ANTHROPIC_URL).mock(return_value=_ok())
    monkeypatch.setenv("CONCIERGE_MODEL", "claude-from-env")

    _llm().complete(system="s", prompt="p")
    assert json.loads(route.calls.last.request.content)["model"] == "claude-from-env"

    _llm(model="claude-explicit").complete(system="s", prompt="p")
    assert json.loads(route.calls.last.request.content)["model"] == "claude-explicit"


@respx.mock
def test_a_json_schema_becomes_structured_output_with_a_strict_schema():
    route = respx.post(ANTHROPIC_URL).mock(return_value=_ok('{"city": "Boston"}'))
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "minLength": 1},
            "meetings": {
                "type": "array",
                "items": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}},
            },
        },
        "required": ["city"],
    }

    text = _llm().complete(system="s", prompt="p", json_schema=schema)

    assert json.loads(text) == {"city": "Boston"}
    sent = json.loads(route.calls.last.request.content)["output_config"]["format"]
    assert sent["type"] == "json_schema"
    assert sent["schema"]["additionalProperties"] is False
    item = sent["schema"]["properties"]["meetings"]["items"]
    assert item["additionalProperties"] is False  # nested objects too
    assert "minLength" not in sent["schema"]["properties"]["city"]  # unsupported keywords are dropped
    assert "minimum" not in item["properties"]["n"]
    assert "additionalProperties" not in schema  # the caller's schema is not mutated


@respx.mock
def test_structured_output_that_is_not_a_json_object_is_an_error():
    respx.post(ANTHROPIC_URL).mock(return_value=_ok("sorry, no"))
    with pytest.raises(LLMError, match="JSON"):
        _llm().complete(system="s", prompt="p", json_schema={"type": "object", "properties": {}})


@respx.mock
def test_an_http_error_is_an_llm_error_that_never_contains_the_key():
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        )
    )
    with pytest.raises(LLMError) as excinfo:
        _llm().complete(system="s", prompt="p")

    assert "401" in str(excinfo.value) and "authentication_error" in str(excinfo.value)
    assert KEY not in str(excinfo.value)


@respx.mock
def test_a_rate_limit_is_retried_then_succeeds():
    route = respx.post(ANTHROPIC_URL).mock(
        side_effect=[httpx.Response(429, json={}), httpx.Response(529, json={}), _ok("finally")]
    )

    assert _llm().complete(system="s", prompt="p") == "finally"
    assert route.call_count == 3


@respx.mock
def test_a_server_error_that_keeps_happening_gives_up_after_the_retries():
    route = respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(500, json={}))

    with pytest.raises(LLMError, match="500"):
        _llm().complete(system="s", prompt="p")
    assert route.call_count == 3  # the first try and two retries


@respx.mock
def test_a_client_error_is_not_retried():
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(400, json={"error": {"type": "invalid_request_error"}})
    )

    with pytest.raises(LLMError):
        _llm().complete(system="s", prompt="p")
    assert route.call_count == 1


@respx.mock
def test_a_network_failure_is_an_llm_error():
    respx.post(ANTHROPIC_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(LLMError, match="ConnectTimeout"):
        _llm().complete(system="s", prompt="p")


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"content": [], "stop_reason": "end_turn"},  # no text block
        {"content": [{"type": "text", "text": "cut o"}], "stop_reason": "max_tokens"},  # truncated
        {"content": [{"type": "text", "text": "no"}], "stop_reason": "refusal"},
        {"stop_reason": "end_turn"},
    ],
)
def test_an_unusable_answer_is_an_llm_error(body):
    respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(LLMError):
        _llm().complete(system="s", prompt="p")


@respx.mock
def test_an_injected_http_client_is_used():
    route = respx.post(ANTHROPIC_URL).mock(return_value=_ok("via injected client"))
    with httpx.Client() as client:
        assert _llm(http=client).complete(system="s", prompt="p") == "via injected client"
    assert route.called


@respx.mock
def test_the_key_and_the_prompt_never_reach_the_logs_and_the_key_never_reaches_a_repr(caplog):
    respx.post(ANTHROPIC_URL).mock(return_value=_ok("ok", usage={"input_tokens": 12, "output_tokens": 3}))
    llm = _llm()

    with caplog.at_level(logging.DEBUG):
        llm.complete(system="s", prompt="Meeting at 199 Water St, New York")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert KEY not in logged and "199 Water St" not in logged
    assert KEY not in repr(llm) and KEY not in str(llm.__dict__.get("_api_key", ""))


# --- FakeLLM -----------------------------------------------------------------------------------


def test_fake_llm_plays_its_script_in_order_and_records_the_calls():
    llm = FakeLLM(["first", "second"])

    assert llm.complete(system="s1", prompt="p1") == "first"
    assert llm.complete(system="s2", prompt="p2", json_schema={"type": "object"}) == "second"
    assert llm.calls == (
        LLMCall(system="s1", prompt="p1", json_schema=None),
        LLMCall(system="s2", prompt="p2", json_schema={"type": "object"}),
    )


def test_fake_llm_raises_scripted_exceptions_and_calls_scripted_functions():
    llm = FakeLLM([LLMError("down"), lambda call: call.prompt.upper()])

    with pytest.raises(LLMError, match="down"):
        llm.complete(system="s", prompt="p")
    assert llm.complete(system="s", prompt="shout") == "SHOUT"


def test_fake_llm_with_an_exhausted_script_fails_loudly_unless_it_has_a_fallback():
    with pytest.raises(LLMError, match="script"):
        FakeLLM([]).complete(system="s", prompt="p")
    assert FakeLLM([], fallback="always").complete(system="s", prompt="p") == "always"
