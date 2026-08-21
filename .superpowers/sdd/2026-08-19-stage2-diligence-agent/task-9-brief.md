### Task 9: Tracing — Tracer protocol, Langfuse adapter, compose file

Spec §9.1: instrument during the build, not after. Everything downstream (agent nodes, tools loop, guardrails, eval) emits through this protocol; Langfuse is one adapter behind it, so tests never need the backend and a Langfuse API drift breaks exactly one file.

**Files:**
- Create: `src/edgar/ops/__init__.py` (empty), `src/edgar/ops/tracing.py`, `docker-compose.langfuse.yml` is NOT hand-written — see Step 5.
- Test: `tests/test_tracing.py`
- Modify: `Makefile` (target `langfuse-up`), `.env` (documented placeholder comments only — no secrets)

**Interfaces:**
- Produces:

```python
class Span(Protocol):
    def event(self, name: str, **attrs) -> None: ...
class Tracer(Protocol):
    trace_id: str
    def span(self, name: str, **attrs) -> AbstractContextManager[Span]: ...
    def flush(self) -> None: ...
class NoopTracer:        # default; trace_id = "trace-" + uuid4().hex[:12]
class RecordingTracer:   # test double; .spans: list[(name, attrs)], .events: list[...]
make_tracer(run_name: str) -> Tracer
# LangfuseTracer if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set
# (after load_secrets_env()), else NoopTracer. Import langfuse lazily.
```

- [ ] **Step 1: Failing tests** — `tests/test_tracing.py`:

```python
from edgar.ops.tracing import NoopTracer, RecordingTracer, make_tracer

def test_noop_supports_nesting_and_flush():
    t = NoopTracer()
    with t.span("outer", cik=1) as s:
        s.event("tool_call", tool="get_facts")
        with t.span("inner") as s2:
            s2.event("x")
    t.flush()
    assert t.trace_id.startswith("trace-")

def test_recording_tracer_captures_spans_and_events():
    t = RecordingTracer()
    with t.span("retrieve", section="growth") as s:
        s.event("tool_call", tool="compute")
    assert ("retrieve", {"section": "growth"}) in t.spans
    assert t.events == [("retrieve", "tool_call", {"tool": "compute"})]

def test_make_tracer_defaults_to_noop(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert isinstance(make_tracer("memo"), NoopTracer)
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/ops/tracing.py`:

```python
import os
import uuid
from contextlib import contextmanager
from typing import Protocol
from collections.abc import Iterator


class Span(Protocol):
    def event(self, name: str, **attrs) -> None: ...


class Tracer(Protocol):
    trace_id: str
    def span(self, name: str, **attrs): ...
    def flush(self) -> None: ...


class _NoopSpan:
    def event(self, name: str, **attrs) -> None:
        pass


class NoopTracer:
    def __init__(self) -> None:
        self.trace_id = "trace-" + uuid.uuid4().hex[:12]

    @contextmanager
    def span(self, name: str, **attrs) -> Iterator[_NoopSpan]:
        yield _NoopSpan()

    def flush(self) -> None:
        pass


class _RecordingSpan:
    def __init__(self, tracer: "RecordingTracer", name: str) -> None:
        self._tracer, self._name = tracer, name

    def event(self, name: str, **attrs) -> None:
        self._tracer.events.append((self._name, name, attrs))


class RecordingTracer:
    def __init__(self) -> None:
        self.trace_id = "trace-test"
        self.spans: list[tuple[str, dict]] = []
        self.events: list[tuple[str, str, dict]] = []

    @contextmanager
    def span(self, name: str, **attrs):
        self.spans.append((name, attrs))
        yield _RecordingSpan(self, name)

    def flush(self) -> None:
        pass


class LangfuseTracer:
    """Thin adapter over the langfuse SDK (v4, OTEL-based). If the SDK's
    surface differs at implementation time, THIS file is the only one that
    changes — consult https://langfuse.com/docs/sdk/python and keep the
    protocol identical."""

    def __init__(self, run_name: str) -> None:
        from langfuse import Langfuse
        self._lf = Langfuse()             # reads LANGFUSE_* env vars
        self._run_name = run_name
        self.trace_id = "trace-" + uuid.uuid4().hex[:12]

    @contextmanager
    def span(self, name: str, **attrs):
        with self._lf.start_as_current_span(name=name) as lf_span:
            lf_span.update(metadata={**attrs, "run": self._run_name,
                                     "local_trace_id": self.trace_id})

            class _S:
                def event(_self, ev_name: str, **ev_attrs) -> None:
                    lf_span.update(metadata={f"event:{ev_name}": ev_attrs})
            yield _S()

    def flush(self) -> None:
        self._lf.flush()


def make_tracer(run_name: str) -> Tracer:
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and \
            os.environ.get("LANGFUSE_SECRET_KEY"):
        try:
            return LangfuseTracer(run_name)
        except Exception as exc:          # backend down ≠ agent down
            print(f"WARNING: langfuse unavailable ({exc}); tracing disabled")
    return NoopTracer()
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_tracing.py -q` → PASS; full suite.

- [ ] **Step 5: Backend setup (documented, not scripted)** — add to `Makefile`:

```make
langfuse-up:
	@test -d ../langfuse || git clone https://github.com/langfuse/langfuse.git ../langfuse
	cd ../langfuse && docker compose up -d
	@echo "Langfuse at http://localhost:3000 — create an org/project, then put"
	@echo "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST=http://localhost:3000 in .env"
```

Append to `.env` (comments + empty placeholders only — values are user-supplied and gitignored anyway):

```
# Tracing (optional): populate after `make langfuse-up` + creating a project.
# LANGFUSE_PUBLIC_KEY=
# LANGFUSE_SECRET_KEY=
# LANGFUSE_HOST=http://localhost:3000
# LLM access for agent/eval runs:
# ANTHROPIC_API_KEY=
```

Run `make langfuse-up` once, confirm the UI loads, create keys, verify `make memo` (after Task 14) produces a visible trace. This is a manual checkpoint, not a test.

- [ ] **Step 6: Commit**

```bash
git add src/edgar/ops tests/test_tracing.py Makefile
git commit -m "feat(ops): Tracer protocol with Langfuse adapter and noop fallback

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

