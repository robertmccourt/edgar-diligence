from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel


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
