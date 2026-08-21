### Task 13: LLM adapter — `LLMClient` protocol, `AnthropicLLM`, `FakeLLM`

The seam that keeps every agent and eval test offline. Two operations cover everything Stage 2 needs: a tool-use turn and a schema-validated structured parse.

**Files:**
- Create: `src/edgar/agent/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ToolCall: id: str; name: str; input: dict
@dataclass(frozen=True)
class LLMTurn:
    text: str                      # concatenated text blocks
    tool_calls: list[ToolCall]     # empty when the model is done
    raw_content: object            # provider content to echo back verbatim
    usage_in: int; usage_out: int

class LLMClient(Protocol):
    def tool_turn(self, *, system: str, messages: list[dict],
                  tools: list[dict]) -> LLMTurn: ...
    def parse_structured(self, *, system: str, prompt: str,
                         output_model: type[BaseModel]) -> BaseModel: ...

class AnthropicLLM:                # real client
    def __init__(self, model: str, max_tokens: int = 16000): ...

class FakeLLM:                     # scripted; raises when script exhausted
    def __init__(self, turns: list[LLMTurn] = (),
                 parsed: list[BaseModel] = ()): ...
```

`AnthropicLLM` rules (from the claude-api reference, current as of 2026-08): `anthropic.Anthropic()` resolves credentials from env; **omit the `thinking` parameter entirely** (Opus 5 / Sonnet 5 default to adaptive; `budget_tokens` returns 400); no assistant prefill; `tool_turn` uses `client.messages.create(model=…, max_tokens=…, system=…, tools=…, messages=…)` and maps `response.content` blocks (`block.type == "text"` → text, `== "tool_use"` → `ToolCall(block.id, block.name, block.input)`); `parse_structured` uses `client.messages.parse(model=…, max_tokens=…, system=…, messages=[{"role":"user","content":prompt}], output_format=output_model)` and returns `response.parsed_output`. Wrap provider errors: catch `anthropic.APIStatusError`/`APIConnectionError` and re-raise as `LLMError(RuntimeError)` with the message — callers never import the anthropic package.

- [ ] **Step 1: Failing tests** — `tests/test_llm.py` (FakeLLM only; AnthropicLLM is exercised by real runs):

```python
import pytest
from pydantic import BaseModel
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall

class _Out(BaseModel):
    answer: str

def test_fake_llm_plays_script_in_order():
    t1 = LLMTurn(text="", tool_calls=[ToolCall("t1", "get_facts", {"cik": 1})],
                 raw_content=[], usage_in=10, usage_out=5)
    t2 = LLMTurn(text="done", tool_calls=[], raw_content=[], usage_in=8,
                 usage_out=4)
    llm = FakeLLM(turns=[t1, t2], parsed=[_Out(answer="hi")])
    assert llm.tool_turn(system="s", messages=[], tools=[]).tool_calls[0].name \
        == "get_facts"
    assert llm.tool_turn(system="s", messages=[], tools=[]).text == "done"
    assert llm.parse_structured(system="s", prompt="p",
                                output_model=_Out).answer == "hi"

def test_fake_llm_raises_when_script_exhausted():
    llm = FakeLLM()
    with pytest.raises(AssertionError, match="script exhausted"):
        llm.tool_turn(system="s", messages=[], tools=[])

def test_fake_llm_records_calls_for_assertions():
    llm = FakeLLM(turns=[LLMTurn("x", [], [], 1, 1)])
    llm.tool_turn(system="SYS", messages=[{"role": "user", "content": "u"}],
                  tools=[{"name": "compute"}])
    assert llm.calls[0]["system"] == "SYS"
    assert llm.calls[0]["tools"][0]["name"] == "compute"
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/agent/llm.py`:

```python
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
        try:
            resp = self._client.messages.parse(
                model=self._model, max_tokens=self._max_tokens, system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=output_model)
        except (self._anthropic.APIStatusError,
                self._anthropic.APIConnectionError) as exc:
            raise LLMError(str(exc)) from exc
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
            f"scripted {type(out).__name__} != requested {output_model.__name__}"
        return out
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_llm.py -q` → PASS; full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/agent/llm.py tests/test_llm.py
git commit -m "feat(agent): LLMClient protocol with Anthropic adapter and scripted fake

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

