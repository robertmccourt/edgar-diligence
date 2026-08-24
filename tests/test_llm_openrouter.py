"""OpenRouterLLM wire-format translation, offline via httpx.MockTransport.

The adapter's contract: graph nodes speak Anthropic shapes (tool_result
block lists, opaque raw_content replay) and must work unchanged against
OpenRouter's OpenAI-style chat completions endpoint.
"""
import json

import httpx
import pytest
from pydantic import BaseModel

from edgar.agent.llm import (AnthropicLLM, LLMError, OpenRouterLLM, ToolCall,
                             make_llm)

TOOLS = [{"name": "get_facts", "description": "fetch facts",
          "input_schema": {"type": "object",
                           "properties": {"cik": {"type": "integer"}}}}]


class Out(BaseModel):
    a: int
    b: str


def _resp(msg, usage=None):
    return {"choices": [{"message": msg}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}


def _client(responses, **kwargs):
    """responses: list of (status, body). Returns (llm, captured_payloads)."""
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    llm = OpenRouterLLM("test/model:free", api_key="k",
                        transport=httpx.MockTransport(handler), **kwargs)
    return llm, sent


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterLLM("test/model:free")


def test_make_llm_routes_by_model_id_shape(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(make_llm("z-ai/glm-5.2:free"), OpenRouterLLM)
    assert isinstance(make_llm("claude-opus-5"), AnthropicLLM)


def test_tool_turn_translates_both_wire_formats():
    assistant_msg = {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "get_facts",
                                                  "arguments":
                                                  '{"cik": 320193}'}}]}
    llm, sent = _client([(200, _resp(assistant_msg))])
    history = [
        {"role": "user", "content": "rubric text"},
        {"role": "assistant", "content": {"role": "assistant",
                                          "content": "prior turn"}},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c0",
             "content": '{"facts": []}'}]},
    ]
    turn = llm.tool_turn(system="sys", messages=history, tools=TOOLS)

    payload = sent[0]
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert payload["messages"][0]["content"] == "sys"
    assert payload["messages"][3] == {"role": "tool", "tool_call_id": "c0",
                                      "content": '{"facts": []}'}
    assert payload["tools"] == [{"type": "function", "function": {
        "name": "get_facts", "description": "fetch facts",
        "parameters": TOOLS[0]["input_schema"]}}]

    assert turn.tool_calls == [ToolCall("c1", "get_facts", {"cik": 320193})]
    assert turn.text == ""
    assert turn.raw_content == assistant_msg  # replayable next turn
    assert (turn.usage_in, turn.usage_out) == (10, 5)


def test_tool_turn_text_only():
    llm, _ = _client([(200, _resp({"role": "assistant",
                                   "content": "done, no more tools"}))])
    turn = llm.tool_turn(system="s", messages=[
        {"role": "user", "content": "q"}], tools=TOOLS)
    assert turn.text == "done, no more tools"
    assert turn.tool_calls == []


def test_tool_turn_bad_arguments_raises():
    msg = {"role": "assistant", "content": None,
           "tool_calls": [{"id": "c1", "type": "function",
                           "function": {"name": "get_facts",
                                        "arguments": "not json"}}]}
    llm, _ = _client([(200, _resp(msg))])
    with pytest.raises(LLMError, match="unparseable tool arguments"):
        llm.tool_turn(system="s", messages=[
            {"role": "user", "content": "q"}], tools=TOOLS)


def test_provider_error_in_200_body_raises():
    llm, _ = _client([(200, {"error": {"message": "model overloaded"}})])
    with pytest.raises(LLMError, match="openrouter error"):
        llm.tool_turn(system="s", messages=[
            {"role": "user", "content": "q"}], tools=TOOLS)


def test_parse_structured_happy_path_with_fences():
    body = _resp({"role": "assistant",
                  "content": '```json\n{"a": 1, "b": "x"}\n```'})
    llm, sent = _client([(200, body)])
    out = llm.parse_structured(system="s", prompt="p", output_model=Out)
    assert out == Out(a=1, b="x")
    assert sent[0]["response_format"]["type"] == "json_schema"
    assert "JSON Schema" in sent[0]["messages"][1]["content"]


def test_parse_structured_retries_without_response_format():
    good = _resp({"role": "assistant", "content": '{"a": 2, "b": "y"}'})
    llm, sent = _client([(400, {"error": "response_format unsupported"}),
                         (200, good)])
    out = llm.parse_structured(system="s", prompt="p", output_model=Out)
    assert out == Out(a=2, b="y")
    assert len(sent) == 2
    assert "response_format" in sent[0]
    assert "response_format" not in sent[1]


def test_parse_structured_never_retries_rate_limit():
    llm, sent = _client([(429, {"error": "free quota exhausted"})])
    with pytest.raises(LLMError, match="429"):
        llm.parse_structured(system="s", prompt="p", output_model=Out)
    assert len(sent) == 1  # a retry would burn the daily free budget


def test_post_backs_off_on_upstream_contention():
    good = _resp({"role": "assistant", "content": "recovered"})
    llm, sent = _client([
        (429, {"error": {"metadata":
                         {"raw": "temporarily rate-limited upstream"}}}),
        (503, {"error": "provider warming up"}),
        (200, good)])
    waits = []
    llm._sleep = waits.append
    turn = llm.tool_turn(system="s", messages=[
        {"role": "user", "content": "q"}], tools=TOOLS)
    assert turn.text == "recovered"
    assert len(sent) == 3
    assert waits == [5, 10]  # exponential backoff between attempts


def test_post_retries_transient_error_in_200_body():
    good = _resp({"role": "assistant", "content": "recovered"})
    llm, sent = _client([
        (200, {"error": {"message": "Upstream error from Nvidia: "
                         "Service temporarily overloaded", "code": 502}}),
        (200, good)])
    llm._sleep = lambda s: None
    turn = llm.tool_turn(system="s", messages=[
        {"role": "user", "content": "q"}], tools=TOOLS)
    assert turn.text == "recovered"
    assert len(sent) == 2


def test_post_gives_up_after_max_tries():
    upstream = (429, {"error": {"metadata":
                                {"raw": "rate-limited upstream"}}})
    llm, sent = _client([upstream] * 4)
    llm._sleep = lambda s: None
    with pytest.raises(LLMError, match="429"):
        llm.tool_turn(system="s", messages=[
            {"role": "user", "content": "q"}], tools=TOOLS)
    assert len(sent) == 4


def test_parse_structured_invalid_payload_raises():
    body = _resp({"role": "assistant", "content": '{"a": "not-an-int"}'})
    llm, _ = _client([(200, body), (200, body)])
    with pytest.raises(LLMError, match="validation"):
        llm.parse_structured(system="s", prompt="p", output_model=Out)


def test_parse_structured_no_json_raises():
    body = _resp({"role": "assistant", "content": "I cannot answer."})
    llm, _ = _client([(200, body), (200, body)])
    with pytest.raises(LLMError, match="no JSON object"):
        llm.parse_structured(system="s", prompt="p", output_model=Out)


def _upstream():
    return (429, {"error": {"metadata":
                            {"raw": "rate-limited upstream"}}})


def test_patient_mode_outlasts_long_saturation():
    """Policy: wait for the pinned high-quality model, don't downgrade.
    With a long patience budget the adapter rides out extended free-pool
    saturation, backing off at a 90s cap between attempts."""
    good = _resp({"role": "assistant", "content": "finally"})
    llm, sent = _client([_upstream()] * 10 + [(200, good)],
                        patience_s=3600.0)
    waits = []
    llm._sleep = waits.append
    turn = llm.tool_turn(system="s", messages=[
        {"role": "user", "content": "q"}], tools=TOOLS)
    assert turn.text == "finally"
    assert len(sent) == 11
    assert max(waits) == 90.0  # capped backoff, not unbounded doubling


def test_patient_mode_gives_up_when_patience_spent():
    llm, sent = _client([_upstream()] * 60, patience_s=600.0)
    waits = []
    llm._sleep = waits.append
    with pytest.raises(LLMError, match="429"):
        llm.tool_turn(system="s", messages=[
            {"role": "user", "content": "q"}], tools=TOOLS)
    assert sum(waits) <= 600.0
    assert len(sent) >= 8  # kept trying well past the quick-fail default


def test_model_withdrawn_404_fails_fast_despite_patience():
    """A 404 means the model was withdrawn (e.g. gpt-oss-20b:free removed
    from the free tier, 2026-08-22). Waiting can't fix it and silently
    switching models is forbidden — fail immediately and loudly."""
    llm, sent = _client([(404, {"error": {"message":
                                          "This model is unavailable"}})],
                        patience_s=3600.0)
    llm._sleep = lambda s: None
    with pytest.raises(LLMError, match="404"):
        llm.tool_turn(system="s", messages=[
            {"role": "user", "content": "q"}], tools=TOOLS)
    assert len(sent) == 1


def test_quota_429_fails_fast_despite_patience():
    llm, sent = _client([(429, {"error": "free quota exhausted"})],
                        patience_s=3600.0)
    llm._sleep = lambda s: None
    with pytest.raises(LLMError, match="429"):
        llm.tool_turn(system="s", messages=[
            {"role": "user", "content": "q"}], tools=TOOLS)
    assert len(sent) == 1


def test_upstream_404_is_transient_not_withdrawal():
    """Observed 2026-08-23: during free-pool congestion GLM's upstream
    provider (Decart) intermittently returns upstream_404, which OpenRouter
    wraps as HTTP 404. That is a provider flake, not a model withdrawal —
    only a 404 WITHOUT an upstream marker means the model is gone."""
    flake = (404, {"error": {"message": "Provider returned error",
                             "code": 404,
                             "metadata": {"provider_error_code":
                                          "upstream_404"}}})
    good = _resp({"role": "assistant", "content": "recovered"})
    llm, sent = _client([flake, (200, good)], patience_s=3600.0)
    llm._sleep = lambda s: None
    turn = llm.tool_turn(system="s", messages=[
        {"role": "user", "content": "q"}], tools=TOOLS)
    assert turn.text == "recovered"
    assert len(sent) == 2
