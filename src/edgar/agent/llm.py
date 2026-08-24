import json
import sys
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class LLMTurn:
    text: str
    tool_calls: list[ToolCall]
    raw_content: object
    usage_in: int
    usage_out: int


class LLMClient(Protocol):
    def tool_turn(self, *, system: str, messages: list[dict],
                  tools: list[dict]) -> LLMTurn: ...
    def parse_structured(self, *, system: str, prompt: str,
                         output_model: type[BaseModel]) -> BaseModel: ...


class AnthropicLLM:
    """Thin provider adapter. Thinking parameter deliberately omitted:
    claude-opus-5 / claude-sonnet-5 run adaptive thinking by default and
    reject budget_tokens with a 400."""

    def __init__(self, model: str, max_tokens: int = 16000):
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def tool_turn(self, *, system, messages, tools) -> LLMTurn:
        try:
            resp = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens,
                system=system, tools=tools, messages=messages)
        except (self._anthropic.APIStatusError,
                self._anthropic.APIConnectionError) as exc:
            raise LLMError(str(exc)) from exc
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input))
                 for b in resp.content if b.type == "tool_use"]
        return LLMTurn(text=text, tool_calls=calls, raw_content=resp.content,
                       usage_in=resp.usage.input_tokens,
                       usage_out=resp.usage.output_tokens)

    def parse_structured(self, *, system, prompt, output_model):
        """`resp.parsed_output` is Optional on the SDK's `ParsedMessage` —
        a truncated or refused generation returns None rather than an
        instance of `output_model`. Guarded here so the failure surfaces
        as an `LLMError` naming the stop reason, not an opaque
        `AttributeError` two modules away in `write_memo`."""
        try:
            resp = self._client.messages.parse(
                model=self._model, max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=output_model)
        except (self._anthropic.APIStatusError,
                self._anthropic.APIConnectionError) as exc:
            raise LLMError(str(exc)) from exc
        if resp.parsed_output is None:
            raise LLMError("structured parse returned no output "
                           "(stop_reason=" +
                           str(getattr(resp, "stop_reason", "?")) + ")")
        return resp.parsed_output


def _extract_json(text: str) -> str:
    """Free-tier models often wrap JSON in ```json fences or prose even
    when response_format was requested; recover the outermost object."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("no JSON object in structured response: " + text[:200])
    return text[start:end + 1]


class OpenRouterLLM:
    """OpenAI-chat-completions adapter for openrouter.ai.

    Wire-format bridge: graph nodes build Anthropic-shaped histories
    (string user turns, tool_result block lists) and replay `raw_content`
    verbatim as the assistant turn — so this adapter stores its own
    OpenAI-shaped assistant message as raw_content and translates only
    the Anthropic-shaped parts at request time. Nodes never see the
    difference."""

    _URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str, max_tokens: int = 16000,
                 api_key: str | None = None, transport=None,
                 patience_s: float = 35.0):
        import os

        import httpx
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise LLMError("OPENROUTER_API_KEY is not set (put it in .env; "
                           "keys are free at openrouter.ai/keys)")
        import time
        self._httpx = httpx
        self._model = model
        self._max_tokens = max_tokens
        self._patience_s = patience_s
        self._sleep = time.sleep  # injectable for tests
        self._client = httpx.Client(
            timeout=httpx.Timeout(300.0, connect=30.0),
            transport=transport,
            headers={"Authorization": f"Bearer {key}",
                     "X-Title": "edgar-diligence"})

    def _post(self, body: dict) -> dict:
        """Backoff only on transient failures: 502/503, and 429s whose body
        says "upstream" (free-pool contention at the provider, common on
        :free models) — retried with capped exponential backoff until
        `patience_s` is spent. Policy: the pinned model is waited for, at
        length, rather than downgraded to whatever is available. Permanent
        failures fail fast regardless of patience: a quota 429 (daily
        free-request cap) can't be waited out inside a run, and a 404
        means the model was withdrawn — surfacing that beats silently
        switching models. Each retry prints to stderr so a long-running
        monitored job shows what it is waiting on."""
        waited, attempt = 0.0, 0
        while True:
            try:
                resp = self._client.post(self._URL, json=body)
            except self._httpx.HTTPError as exc:
                raise LLMError(str(exc)) from exc
            if resp.status_code == 200:
                data = resp.json()
                if data.get("choices"):
                    return data
                # OpenRouter reports some provider failures in a 200 body,
                # e.g. {"error": {"code": 502, "message": "overloaded"}}
                code = (data.get("error") or {}).get("code")
                detail = "openrouter error: " + json.dumps(data)[:500]
                transient = (code in (502, 503)
                             or (code in (404, 429)
                                 and "upstream" in detail))
            else:
                detail = (f"openrouter {resp.status_code}: "
                          + resp.text[:500])
                # 404/429 with an "upstream" marker is a provider flake
                # (e.g. Decart's upstream_404 during congestion); without
                # it, 404 = model withdrawn and 429 = daily quota — both
                # permanent, both fail fast.
                transient = (resp.status_code in (502, 503)
                             or (resp.status_code in (404, 429)
                                 and "upstream" in resp.text))
            delay = min(90.0, 5.0 * 2 ** attempt)
            if not transient or waited + delay > self._patience_s:
                raise LLMError(detail)
            print(f"openrouter: {self._model} unavailable "
                  f"(attempt {attempt + 1}, {waited / 60:.1f}m waited), "
                  f"retrying in {delay:.0f}s", file=sys.stderr, flush=True)
            self._sleep(delay)
            waited += delay
            attempt += 1

    @staticmethod
    def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if m["role"] == "assistant":
                # raw_content stored by tool_turn: already OpenAI-shaped
                out.append(content if isinstance(content, dict)
                           else {"role": "assistant",
                                 "content": str(content)})
            elif isinstance(content, str):
                out.append({"role": "user", "content": content})
            else:  # Anthropic-shaped tool_result blocks from dispatch_tool
                for block in content:
                    out.append({"role": "tool",
                                "tool_call_id": block["tool_use_id"],
                                "content": block["content"]})
        return out

    @staticmethod
    def _to_openai_tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function",
                 "function": {"name": t["name"],
                              "description": t.get("description", ""),
                              "parameters": t["input_schema"]}}
                for t in tools]

    def tool_turn(self, *, system, messages, tools) -> LLMTurn:
        data = self._post({
            "model": self._model, "max_tokens": self._max_tokens,
            "messages": self._to_openai_messages(system, messages),
            "tools": self._to_openai_tools(tools)})
        msg = data["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            raw_args = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise LLMError(f"unparseable tool arguments from "
                               f"{self._model}: {raw_args[:200]}") from exc
            calls.append(ToolCall(tc["id"], tc["function"]["name"], args))
        usage = data.get("usage") or {}
        return LLMTurn(text=msg.get("content") or "", tool_calls=calls,
                       raw_content=msg,
                       usage_in=usage.get("prompt_tokens", 0),
                       usage_out=usage.get("completion_tokens", 0))

    def parse_structured(self, *, system, prompt, output_model):
        schema = output_model.model_json_schema()
        body = {
            "model": self._model, "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content":
                 prompt + "\n\nRespond with a single JSON object matching "
                 "this JSON Schema, no prose:\n" + json.dumps(schema)}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": output_model.__name__,
                                "schema": schema}}}
        try:
            data = self._post(body)
        except LLMError as exc:
            # Some free endpoints reject response_format; retry without it
            # (the schema is in the prompt too). Never retry quota/auth
            # failures — that burns the daily free-request budget.
            if any(c in str(exc) for c in ("429", "401", "402", "403")):
                raise
            body.pop("response_format", None)
            data = self._post(body)
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        if not text.strip():
            # Observed with kimi-k2.5: reasoning models can spend the whole
            # max_tokens budget thinking and emit no content at all.
            raise LLMError(
                "structured response has empty content (finish_reason="
                f"{choice.get('finish_reason')!r}; a reasoning model may "
                "have spent max_tokens thinking)")
        try:
            return output_model.model_validate_json(_extract_json(text))
        except ValidationError as exc:
            raise LLMError(f"structured response failed "
                           f"{output_model.__name__} validation: "
                           f"{exc}") from exc


def make_llm(model: str, max_tokens: int = 16000,
             patience_s: float = 35.0):
    """Provider routing by model-id shape: OpenRouter ids are
    'vendor/model[:free]'; bare Anthropic ids have no slash. `patience_s`
    is how long OpenRouter calls wait out free-pool saturation before
    failing (the Anthropic SDK manages its own retries)."""
    return (OpenRouterLLM(model, max_tokens, patience_s=patience_s)
            if "/" in model else AnthropicLLM(model, max_tokens))


@dataclass
class FakeLLM:
    """Scripted double. Also records every call for assertions."""
    turns: list[LLMTurn] = field(default_factory=list)
    parsed: list[BaseModel] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    parse_calls: list[dict] = field(default_factory=list)

    def tool_turn(self, *, system, messages, tools) -> LLMTurn:
        self.calls.append({"system": system, "messages": messages,
                           "tools": tools})
        assert self.turns, "FakeLLM script exhausted (tool_turn)"
        return self.turns.pop(0)

    def parse_structured(self, *, system, prompt, output_model):
        self.parse_calls.append({"system": system, "prompt": prompt,
                                 "output_model": output_model})
        assert self.parsed, "FakeLLM script exhausted (parse_structured)"
        out = self.parsed.pop(0)
        assert isinstance(out, output_model), \
            f"scripted {type(out).__name__} != " \
            f"requested {output_model.__name__}"
        return out
