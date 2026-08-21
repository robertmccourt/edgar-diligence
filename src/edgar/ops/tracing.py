import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol


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
        except Exception as exc:          # backend down != agent down
            print(f"WARNING: langfuse unavailable ({exc}); tracing disabled")
    return NoopTracer()
