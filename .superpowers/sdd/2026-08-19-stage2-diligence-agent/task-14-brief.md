### Task 14: Agent nodes, LangGraph wiring, CLI

Spec §7 flow: load memory → coverage → plan → per-section retrieve → ledger → compact-if-over-budget → write → guardrail/repair → emit → persist session+conclusions. Nodes are plain functions over a state dict, unit-tested with `FakeLLM` and `RecordingTracer`; LangGraph only sequences them, so a framework drift breaks one thin file.

**Files:**
- Create: `src/edgar/agent/tool_defs.py`, `src/edgar/agent/nodes.py`, `src/edgar/agent/graph.py`, `src/edgar/agent/run.py`
- Test: `tests/test_agent_nodes.py`, `tests/test_agent_graph.py`
- Modify: `Makefile` (target `memo`)

**Interfaces:**

```python
# edgar.agent.tool_defs — anthropic-format tool schemas + dispatcher
TOOL_DEFS: list[dict]        # 5 entries: get_facts, search_filings, compute,
                             # get_peer_set, list_available_facts. input_schema
                             # WITHOUT as_of — the harness injects it; the model
                             # cannot choose a different date (spec §6).
dispatch_tool(con, name: str, args: dict, *, as_of: date,
              embedder, retrieval_k: int) -> tuple[str, list[LedgerEntry]]
    # returns (json_result_for_model, ledger_entries_to_append)
    # tool errors (ComputeError, ValueError) come back as
    # ('{"error": "..."}', []) — the model sees the message and retries;
    # the harness never crashes on a bad model-supplied argument.

# edgar.agent.nodes — every node: (state: AgentState) -> AgentState
AgentState = TypedDict("AgentState", {...})   # cik, as_of, question, config,
    # con-factory, llm, embedder, tracer, ledger, coverage, plan (slugs),
    # section_idx, memo, guardrail_report, repair_round, conclusions,
    # recalled_ids, usage: dict, company_name
load_memory(state) -> state      # recall_conclusions + note in ledger + recalled_ids
coverage_node(state) -> state    # list_available_facts → ledger 'coverage' entries
plan_node(state) -> state        # section slugs to run: all 11 for memo mode;
                                 # ["qa"] for question mode
retrieve_section(state) -> state # manual tool loop (≤ max_tool_turns), ledger
compact_node(state) -> state     # ledger.compact() when over threshold
write_memo(state) -> state       # parse_structured → Memo (from ledger render)
guardrail_node(state) -> state   # check_memo -> guardrail_report
repair_node(state) -> state      # repair_memo(); repair_round += 1
emit(state) -> state             # render_markdown → file; save_session;
                                 # record_conclusions(learned_as_of=as_of)

# edgar.agent.graph
build_graph() -> compiled LangGraph app
run_agent(*, cik, as_of, question=None, config=None, llm=None, embedder=None,
          tracer=None, con=None, out_dir=Path("data/memos")) -> Memo
    # convenience wrapper: builds state, invokes graph, returns final memo.
    # Defaults construct real deps (AnthropicLLM etc.); tests pass fakes.
```

The retrieve loop inside `retrieve_section` (manual loop per the claude-api reference — chosen over the SDK tool runner because every call must be intercepted for ledger writes, span events, and as_of injection):

```python
messages = [{"role": "user", "content": rubric_plus_context}]
for _ in range(cfg.max_tool_turns):
    turn = llm.tool_turn(system=system_prompt, messages=messages,
                         tools=TOOL_DEFS)
    if not turn.tool_calls:
        break
    messages.append({"role": "assistant", "content": turn.raw_content})
    results = []
    for call in turn.tool_calls:
        span.event("tool_call", tool=call.name)
        payload, entries = dispatch_tool(con, call.name, call.input,
                                         as_of=state["as_of"], ...)
        for e in entries:
            e.section = slug
            ledger.append(e)
        results.append({"type": "tool_result", "tool_use_id": call.id,
                        "content": payload})
    messages.append({"role": "user", "content": results})
```

All tool_result blocks for one assistant turn go back in a SINGLE user message (splitting them silently degrades parallel tool use). The final `turn.text` becomes the section's draft note appended to the ledger as a `note` entry.

`write_memo` prompt = system prompt + full `ledger.render()` + the 11 section titles/slugs + instruction: cite ONLY identifiers present in the ledger, one claim per assertion, set `status="status_code"` with the status note for sections whose ledger evidence is only coverage codes. Output model: `Memo` (Task 12) — `parse_structured(output_model=Memo)` gives schema enforcement for free.

`emit` writes `data/memos/{cik}_{as_of}_{config_version}.md` + `.json` (memo.model_dump_json), saves the session row (recalled ids included), records conclusions: the LLM turn inside `emit` is skipped in v1 — conclusions are the claim texts of the 3 sections with most claims? No: keep deterministic and honest — conclusions = every claim text from sections `growth`, `profitability`, `cash_quality` that survived guardrails, capped at 5. Deterministic, testable, no extra LLM call.

- [ ] **Step 1: Failing tests**

`tests/test_agent_nodes.py` — the load-bearing behaviors, all offline:

```python
from datetime import date
from pydantic import TypeAdapter
from edgar.db import connect, init_schema
from edgar.curate.facts import create_fact_table
from edgar.tools.compute import create_derivation_table
from edgar.narrative.store import create_narrative_tables
from edgar.memory.episodic import create_memory_tables, record_conclusions, save_session
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall
from edgar.agent.ledger import EvidenceLedger
from edgar.agent.memo import Memo, MemoSection, Claim
from edgar.agent.tool_defs import TOOL_DEFS, dispatch_tool
from edgar.agent import nodes
from edgar.narrative.embedder import FakeEmbedder
from edgar.ops.tracing import RecordingTracer
from edgar.agent.agent_config import load_agent_config
# ... _fact helper ...

def _con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_fact_table(con); create_derivation_table(con)
    create_narrative_tables(con); create_memory_tables(con)
    con.execute("""CREATE TABLE company (cik BIGINT, name VARCHAR, sic VARCHAR,
        sector VARCHAR, fiscal_year_end_month INTEGER, first_filing_date DATE,
        eligibility_status VARCHAR, exclusion_reason VARCHAR)""")
    con.execute("INSERT INTO company VALUES (1,'ACME','3571','manufacturing',"
                "12,DATE '2019-01-01','eligible',NULL)")
    return con

def _state(con, **over):
    st = dict(cik=1, as_of=date(2023, 6, 1), question=None,
              config=load_agent_config("v1"), con=con,
              llm=FakeLLM(), embedder=FakeEmbedder(),
              tracer=RecordingTracer(), ledger=EvidenceLedger(),
              coverage=None, plan=[], section_idx=0, memo=None,
              guardrail_report=None, repair_round=0, conclusions=[],
              recalled_ids=[], usage={"in": 0, "out": 0},
              company_name="ACME")
    st.update(over)
    return st

def test_tool_defs_exclude_as_of_everywhere():
    for t in TOOL_DEFS:
        assert "as_of" not in t["input_schema"].get("properties", {}), t["name"]

def test_dispatch_injects_as_of_and_builds_ledger_entries(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    payload, entries = dispatch_tool(
        con, "get_facts",
        {"cik": 1, "fields": ["revenue"], "period_start": "2023-01-01",
         "period_end": "2023-03-31"},
        as_of=date(2023, 6, 1), embedder=FakeEmbedder(), retrieval_k=4)
    assert "fA" in payload
    assert entries[0].kind == "fact" and entries[0].identifier == "fA"

def test_dispatch_tool_error_is_payload_not_crash(tmp_path):
    con = _con(tmp_path)
    payload, entries = dispatch_tool(
        con, "compute", {"expression": "a +", "inputs": {}},
        as_of=date(2023, 6, 1), embedder=FakeEmbedder(), retrieval_k=4)
    assert "error" in payload and entries == []

def test_load_memory_respects_as_of(tmp_path):
    con = _con(tmp_path)
    save_session(con, session_id="s25", cik=1, as_of=date(2025, 1, 1),
                 config_version="v1", trace_id="t", question=None,
                 recalled_conclusion_ids=[])
    record_conclusions(con, session_id="s25", cik=1,
                       conclusions=["from the future"],
                       learned_as_of=date(2025, 1, 1), trace_id="t")
    st = nodes.load_memory(_state(con))
    assert st["recalled_ids"] == []
    assert "from the future" not in st["ledger"].render()

def test_retrieve_section_appends_ledger_and_stops_on_no_tools(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    llm = FakeLLM(turns=[
        LLMTurn(text="", raw_content=[], usage_in=5, usage_out=5,
                tool_calls=[ToolCall("t1", "get_facts",
                                     {"cik": 1, "fields": ["revenue"],
                                      "period_start": "2023-01-01",
                                      "period_end": "2023-03-31"})]),
        LLMTurn(text="Revenue established.", raw_content=[], tool_calls=[],
                usage_in=5, usage_out=5)])
    st = _state(con, llm=llm, plan=["growth"], section_idx=0)
    st = nodes.retrieve_section(st)
    assert "fA" in st["ledger"].identifiers()
    assert st["section_idx"] == 1
    assert any(e.kind == "note" for e in st["ledger"].entries)

def test_write_then_guardrail_then_repair_loop(tmp_path):
    con = _con(tmp_path)
    bad = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
               sections=[MemoSection(slug="growth", title="Growth",
                                     claims=[Claim(text="Revenue was 100")])])
    llm = FakeLLM(parsed=[bad])
    st = _state(con, llm=llm)
    st = nodes.write_memo(st)
    st = nodes.guardrail_node(st)
    assert st["guardrail_report"].rejection_count == 1
    st = nodes.repair_node(st)
    assert st["memo"].sections[0].claims[0].is_hypothesis

def test_emit_persists_session_and_dated_conclusions(tmp_path):
    con = _con(tmp_path)
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      claims=[Claim(text="Revenue was 100",
                                                    citations=["fA"])])],
                config_version="v1+deadbeef", trace_id="tr", session_id="S1")
    st = _state(con, memo=memo)
    st["out_dir"] = tmp_path / "memos"
    nodes.emit(st)
    row = con.execute("SELECT cik, as_of_date FROM session").fetchone()
    assert row == (1, date(2023, 6, 1))
    c = con.execute("SELECT conclusion, learned_as_of "
                    "FROM session_conclusion").fetchone()
    assert c == ("Revenue was 100", date(2023, 6, 1))
    assert (tmp_path / "memos").glob("*.md")
```

`tests/test_agent_graph.py` — one end-to-end offline run:

```python
from datetime import date
from edgar.agent.graph import run_agent
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall
from edgar.agent.memo import Memo, MemoSection, Claim
# ... same _con helper as test_agent_nodes (copy it) ...

def test_full_run_with_fakes_produces_cited_memo(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    # One retrieve turn per planned section (script: tool call then done),
    # then a structured memo whose only citation is the ledgered fact.
    turns = []
    for _ in range(11):
        turns += [LLMTurn("", [ToolCall("t", "get_facts",
                                        {"cik": 1, "fields": ["revenue"],
                                         "period_start": "2023-01-01",
                                         "period_end": "2023-03-31"})],
                          [], 1, 1),
                  LLMTurn("noted", [], [], 1, 1)]
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      claims=[Claim(text="Revenue was 100",
                                                    citations=["fA"])])])
    llm = FakeLLM(turns=turns, parsed=[memo])
    out = run_agent(cik=1, as_of=date(2023, 6, 1), llm=llm, con=con,
                    embedder=__import__("edgar.narrative.embedder",
                                        fromlist=["FakeEmbedder"]).FakeEmbedder(),
                    out_dir=tmp_path / "memos")
    assert out.sections[0].claims[0].citations == ["fA"]
    assert out.session_id and out.trace_id and out.config_version
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — three files. `tool_defs.py` first:

```python
import json
from datetime import date, datetime
from edgar.agent.ledger import LedgerEntry
from edgar.tools.facts_tools import get_facts, list_available_facts
from edgar.tools.compute import compute, ComputeError
from edgar.tools.peers import get_peer_set
from edgar.narrative.store import search_spans

TOOL_DEFS: list[dict] = [
    {"name": "get_facts",
     "description": "Canonical financial facts for one company and period "
                    "window. Missing fields come back with a status code — "
                    "cite fact_id values verbatim.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"},
         "fields": {"type": "array", "items": {"type": "string"}},
         "period_start": {"type": "string", "description": "YYYY-MM-DD"},
         "period_end": {"type": "string", "description": "YYYY-MM-DD"}},
      "required": ["cik", "fields", "period_start", "period_end"]}},
    {"name": "search_filings",
     "description": "Search 10-K narrative (Items 1, 1A, 7). Returns spans "
                    "with span_id to cite.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}, "query": {"type": "string"},
         "items": {"type": "array", "items": {"type": "string"}}},
      "required": ["cik", "query"]}},
    {"name": "compute",
     "description": "The ONLY way to derive a number. Expression over "
                    "variables bound to fact_ids; returns value + "
                    "derivation_id to cite. + and - require like period "
                    "types; ratios may mix.",
     "input_schema": {"type": "object", "properties": {
         "expression": {"type": "string"},
         "inputs": {"type": "object",
                    "additionalProperties": {"type": "string"}}},
      "required": ["expression", "inputs"]}},
    {"name": "get_peer_set",
     "description": "Comparable companies by SIC, with the selection rule.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}, "min_peers": {"type": "integer"}},
      "required": ["cik"]}},
    {"name": "list_available_facts",
     "description": "Coverage map: which fields exist for which periods, "
                    "with status codes. Consult before claiming absence.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}},
      "required": ["cik"]}},
]


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def dispatch_tool(con, name, args, *, as_of, embedder, retrieval_k):
    try:
        if name == "get_facts":
            r = get_facts(con, int(args["cik"]), list(args["fields"]),
                          _d(args["period_start"]), _d(args["period_end"]),
                          as_of)
            entries = [LedgerEntry("fact", f.fact_id,
                                   f"{f.canonical_field} {f.period_end} "
                                   f"{f.unit} {f.value:g} "
                                   f"(filed {f.filed_date})", "")
                       for f in r.facts]
            return r.model_dump_json(), entries
        if name == "search_filings":
            hits = search_spans(con, args["query"], int(args["cik"]), as_of,
                                k=retrieval_k, embedder=embedder,
                                items=args.get("items"))
            entries = [LedgerEntry("span", h.span_id,
                                   f"{h.item} {h.accession}: "
                                   f"{h.text[:120]}", "",
                                   payload=h.text)
                       for h in hits]
            return json.dumps([h.model_dump(mode="json") for h in hits]), entries
        if name == "compute":
            c = compute(con, args["expression"], dict(args["inputs"]), as_of)
            entry = LedgerEntry("derivation", c.derivation_id,
                                f"{c.expression} = {c.value:g} "
                                f"inputs {c.inputs}", "")
            return c.model_dump_json(), [entry]
        if name == "get_peer_set":
            ps = get_peer_set(con, int(args["cik"]), as_of,
                              min_peers=int(args.get("min_peers", 10)))
            return ps.model_dump_json(), [LedgerEntry(
                "note", "", f"peer set ({len(ps.peers)}): "
                f"{ps.selection_rule}", "")]
        if name == "list_available_facts":
            rep = list_available_facts(con, int(args["cik"]), as_of)
            gist = "; ".join(
                f"{e.period_end}: " + ",".join(
                    f"{k}={v}" for k, v in sorted(e.statuses.items())
                    if v != "AVAILABLE")
                for e in rep.entries[:8]) or "all AVAILABLE"
            return rep.model_dump_json(), [LedgerEntry(
                "coverage", "", f"coverage: {gist}", "")]
        return json.dumps({"error": f"unknown tool {name}"}), []
    except (ComputeError, ValueError, KeyError, TypeError) as exc:
        return json.dumps({"error": str(exc)}), []
```

`nodes.py` — full implementation (the retrieve loop is the snippet above made concrete):

```python
import json
import uuid
from pathlib import Path
from edgar.agent.ledger import LedgerEntry
from edgar.agent.memo import Memo, render_markdown
from edgar.agent.guardrails import check_memo, repair_memo
from edgar.agent.tool_defs import TOOL_DEFS, dispatch_tool
from edgar.memory.procedural import SECTIONS, load_system_prompt, load_rubric
from edgar.memory.episodic import (
    recall_conclusions, save_session, record_conclusions)
from edgar.tools.facts_tools import list_available_facts

_CONCLUSION_SECTIONS = ("growth", "profitability", "cash_quality")


def load_memory(state: dict) -> dict:
    state["session_id"] = "S-" + uuid.uuid4().hex[:12]
    recalled = recall_conclusions(state["con"], state["cik"],
                                  state["as_of"],
                                  limit=state["config"].recall_limit)
    state["recalled_ids"] = [c.conclusion_id for c in recalled]
    for c in recalled:
        state["ledger"].append(LedgerEntry(
            "note", "", f"prior conclusion (learned {c.learned_as_of}): "
            f"{c.conclusion}", "memory"))
    return state


def coverage_node(state: dict) -> dict:
    rep = list_available_facts(state["con"], state["cik"], state["as_of"])
    state["coverage"] = rep
    gaps = sorted({f"{f}={st}" for e in rep.entries
                   for f, st in e.statuses.items() if st != "AVAILABLE"})
    state["ledger"].append(LedgerEntry(
        "coverage", "", "coverage gaps: " + ("; ".join(gaps) or "none"),
        "unanswered"))
    return state


def plan_node(state: dict) -> dict:
    state["plan"] = (["qa"] if state["question"]
                     else [slug for _, slug, _ in SECTIONS])
    return state


def _rubric_for(state: dict, slug: str) -> str:
    if slug == "qa":
        return ("Answer this question from tool evidence only, with "
                "citations; refuse with status codes when the store cannot "
                f"answer: {state['question']}")
    return load_rubric(slug)


def retrieve_section(state: dict) -> dict:
    cfg, slug = state["config"], state["plan"][state["section_idx"]]
    system = load_system_prompt() +         f"

Company CIK {state['cik']}, as_of {state['as_of']}."
    messages = [{"role": "user", "content": _rubric_for(state, slug)}]
    with state["tracer"].span(f"section:{slug}", cik=state["cik"]) as span:
        for _ in range(cfg.max_tool_turns):
            turn = state["llm"].tool_turn(system=system, messages=messages,
                                          tools=TOOL_DEFS)
            state["usage"]["in"] += turn.usage_in
            state["usage"]["out"] += turn.usage_out
            if not turn.tool_calls:
                if turn.text:
                    state["ledger"].append(
                        LedgerEntry("note", "", turn.text[:800], slug))
                break
            messages.append({"role": "assistant",
                             "content": turn.raw_content})
            results = []
            for call in turn.tool_calls:
                span.event("tool_call", tool=call.name)
                payload, entries = dispatch_tool(
                    state["con"], call.name, call.input,
                    as_of=state["as_of"], embedder=state["embedder"],
                    retrieval_k=cfg.retrieval_k)
                for e in entries:
                    e.section = slug
                    state["ledger"].append(e)
                results.append({"type": "tool_result",
                                "tool_use_id": call.id, "content": payload})
            messages.append({"role": "user", "content": results})
    state["section_idx"] += 1
    return state


def compact_node(state: dict) -> dict:
    cfg = state["config"]
    if state["ledger"].size_chars() > cfg.compaction_threshold_chars:
        freed = state["ledger"].compact()
        with state["tracer"].span("compaction") as span:
            span.event("compaction", freed=freed)
    return state


def write_memo(state: dict) -> dict:
    cfg = state["config"]
    section_list = "
".join(f"{n}. {slug}: {title}"
                             for n, slug, title in SECTIONS)
    prompt = (
        "Write the diligence memo as structured output.
"
        "Sections (use these slugs/titles, in order):
" + section_list +
        "

RULES: cite ONLY identifiers that appear in [brackets] in the "
        "evidence ledger below, copied verbatim. One assertion per claim. "
        "A section whose evidence is only status codes gets "
        "status='status_code' and a status_note. Value-creation ideas go "
        "in hypotheses, labeled.

EVIDENCE LEDGER:
" +
        state["ledger"].render()[:cfg.context_budget_chars])
    memo = state["llm"].parse_structured(
        system=load_system_prompt(), prompt=prompt, output_model=Memo)
    state["memo"] = memo.model_copy(update={
        "cik": state["cik"], "as_of": state["as_of"],
        "company_name": state["company_name"],
        "config_version": cfg.config_version,
        "trace_id": state["tracer"].trace_id,
        "session_id": state["session_id"]})
    return state


def guardrail_node(state: dict) -> dict:
    report = check_memo(state["con"], state["memo"], state["as_of"])
    state["guardrail_report"] = report
    with state["tracer"].span("guardrails") as span:
        span.event("guardrail", rejections=report.rejection_count)
    return state


def repair_node(state: dict) -> dict:
    state["memo"] = repair_memo(state["memo"], state["guardrail_report"])
    state["repair_round"] += 1
    return state


def emit(state: dict) -> dict:
    memo, con = state["memo"], state["con"]
    out_dir = Path(state.get("out_dir", "data/memos"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{memo.cik}_{memo.as_of}_{memo.config_version}"
    (out_dir / f"{stem}.md").write_text(render_markdown(memo))
    (out_dir / f"{stem}.json").write_text(json.dumps({
        "memo": memo.model_dump(mode="json"),
        "guardrail_rejections": state["guardrail_report"].rejection_count
        if state["guardrail_report"] else 0,
        "usage": state["usage"]}))
    save_session(con, session_id=memo.session_id, cik=memo.cik,
                 as_of=memo.as_of, config_version=memo.config_version,
                 trace_id=memo.trace_id, question=state["question"],
                 recalled_conclusion_ids=state["recalled_ids"])
    conclusions = [c.text for s in memo.sections
                   if s.slug in _CONCLUSION_SECTIONS
                   for c in s.claims if not c.is_hypothesis][:5]
    record_conclusions(con, session_id=memo.session_id, cik=memo.cik,
                       conclusions=conclusions, learned_as_of=memo.as_of,
                       trace_id=memo.trace_id)
    state["tracer"].flush()
    return state
```

Note `emit` reads `state.get("out_dir", …)` — tests set `state["out_dir"]`; `run_agent` passes its `out_dir` argument through the initial state. `guardrail_report` may be None only in the emit-without-guardrail test path; production flow always runs guardrail_node first.

`graph.py`:

```python
from pathlib import Path
from langgraph.graph import StateGraph, START, END
from edgar.agent import nodes

def build_graph():
    g = StateGraph(dict)
    for name in ("load_memory", "coverage_node", "plan_node",
                 "retrieve_section", "compact_node", "write_memo",
                 "guardrail_node", "repair_node", "emit"):
        g.add_node(name, getattr(nodes, name))
    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "coverage_node")
    g.add_edge("coverage_node", "plan_node")
    g.add_edge("plan_node", "retrieve_section")
    g.add_edge("retrieve_section", "compact_node")
    g.add_conditional_edges(
        "compact_node",
        lambda s: "retrieve_section" if s["section_idx"] < len(s["plan"])
        else "write_memo")
    g.add_edge("write_memo", "guardrail_node")
    g.add_conditional_edges(
        "guardrail_node",
        lambda s: "emit" if not s["guardrail_report"].rejection_count
        or s["repair_round"] >= s["config"].max_repair_rounds
        else "repair_node")
    g.add_edge("repair_node", "guardrail_node")
    g.add_edge("emit", END)
    return g.compile()
```

plus `run_agent(...)` assembling defaults (real `connect()`, `AnthropicLLM(cfg.generation_model)`, `SentenceTransformerEmbedder()` only when a search is possible — pass `FakeEmbedder` replacement via arg in tests), calling `build_graph().invoke(state)`, returning `state["memo"]`. `run.py`:

```python
"""CLI: venv/bin/python -m edgar.agent.run --cik 320193 --as-of 2024-03-01
        [--question "..."] [--config v1]"""
import argparse
from datetime import date
from edgar.config import load_secrets_env
from edgar.agent.graph import run_agent

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cik", type=int, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--question", default=None)
    p.add_argument("--config", default="v1")
    a = p.parse_args()
    load_secrets_env()
    memo = run_agent(cik=a.cik, as_of=date.fromisoformat(a.as_of),
                     question=a.question)
    print(f"memo written: data/memos/ (session {memo.session_id}, "
          f"trace {memo.trace_id})")

if __name__ == "__main__":
    main()
```

Makefile:

```make
memo:
	venv/bin/python -m edgar.agent.run --cik $(CIK) --as-of $(AS_OF)
```

**LangGraph sanity check before wiring:** run `venv/bin/python -c "from langgraph.graph import StateGraph, START, END; print('ok')"`. If that import fails on the pinned version, do NOT fight the framework — `graph.py` alternatively ships `build_graph()` returning a tiny sequential driver with the same `.invoke(state)` surface (a 20-line loop over the same node order and the same two conditionals). The nodes and tests do not change either way; note the choice in the commit.

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_agent_nodes.py tests/test_agent_graph.py -q` → PASS; full suite.

- [ ] **Step 5: First real memo (manual checkpoint, costs ~$1-3)** — requires `ANTHROPIC_API_KEY` in `.env`, the rebuilt store (Task 1), narrative index (Task 7):

```bash
make memo CIK=320193 AS_OF=2024-03-01
```

Open `data/memos/320193_2024-03-01_*.md`. Verify: sections present, citations bracketed, section 11 lists real status codes, no number appears without a citation. If Langfuse is up (Task 9), confirm the trace shows per-section spans with tool_call events. Fix obvious prompt problems in `prompts/` (that changes `config_version` — expected and correct).

- [ ] **Step 6: Commit**

```bash
git add src/edgar/agent tests/test_agent_nodes.py tests/test_agent_graph.py Makefile
git commit -m "feat(agent): section loop, LangGraph wiring, CLI; first real memo

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

