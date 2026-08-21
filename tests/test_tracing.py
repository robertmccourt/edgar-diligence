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
