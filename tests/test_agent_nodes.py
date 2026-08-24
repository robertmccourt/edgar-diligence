from datetime import date

from edgar.db import connect, init_schema
from edgar.curate.facts import create_fact_table
from edgar.curate.mapping import create_mapping_table
from edgar.narrative.store import create_narrative_tables
from edgar.memory.episodic import (
    create_memory_tables, record_conclusions, save_session)
from edgar.tools.compute import create_derivation_table
from edgar.agent import nodes
from edgar.agent.agent_config import load_agent_config
from edgar.agent.ledger import EvidenceLedger, LedgerEntry
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall
from edgar.agent.memo import Claim, Memo, MemoSection
from edgar.agent.tool_defs import TOOL_DEFS, dispatch_tool
from edgar.narrative.embedder import FakeEmbedder
from edgar.ops.tracing import RecordingTracer


def _fact(con, *, fact_id, cik=1, field="revenue", value=100.0, unit="USD",
          ptype="duration", pstart=date(2023, 1, 1), pend=date(2023, 3, 31),
          filed=date(2023, 5, 1), accn="a-1", tag="Revenues", rule="MR-0003"):
    con.execute(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [fact_id, cik, field, value, unit, ptype, pstart, pend,
         "2023", "Q1", filed, accn, tag, rule, 1.0, "2023q2"],
    )


def _con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con)
    create_fact_table(con)
    create_mapping_table(con)
    create_derivation_table(con)
    create_narrative_tables(con)
    create_memory_tables(con)
    con.execute("""CREATE TABLE company (cik BIGINT, name VARCHAR,
        sic VARCHAR, sector VARCHAR, fiscal_year_end_month INTEGER,
        first_filing_date DATE, eligibility_status VARCHAR,
        exclusion_reason VARCHAR)""")
    con.execute("INSERT INTO company VALUES (1,'ACME','3571',"
                "'manufacturing',12,DATE '2019-01-01','eligible',NULL)")
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
        assert "as_of" not in t["input_schema"].get("properties", {}), \
            t["name"]


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
                                     claims=[Claim(
                                         text="Revenue was 100")])])
    llm = FakeLLM(parsed=[bad])
    st = _state(con, llm=llm)
    st["session_id"] = "S-test"
    st = nodes.write_memo(st)
    st = nodes.guardrail_node(st)
    assert st["guardrail_report"].rejection_count == 1
    st = nodes.repair_node(st)
    assert st["memo"].sections[0].claims[0].is_hypothesis


def test_write_memo_truncates_ledger_on_whole_lines(tmp_path):
    con = _con(tmp_path)
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
               sections=[])
    llm = FakeLLM(parsed=[memo])
    st = _state(con, llm=llm)
    st["session_id"] = "S-test"
    for i in range(10):
        st["ledger"].append(LedgerEntry(
            "fact", f"f{i:04d}", "revenue grew strongly this quarter",
            "growth"))
    full = st["ledger"].render()
    first_line = full.split("\n")[0]
    # room for exactly the first whole line, nothing more
    st["config"] = st["config"].model_copy(update={
        "context_budget_chars": len(first_line) + 5})

    st = nodes.write_memo(st)

    prompt = llm.parse_calls[0]["prompt"]
    assert first_line in prompt          # kept line is complete, unmangled
    assert "[f0001]" not in prompt        # everything past it was dropped
    assert "9 ledger lines omitted — over context budget" in prompt
    # no partial identifier: nothing that looks like a cut-off "[f000" or
    # a fragment of the gist text should appear
    assert "[f000" not in prompt.replace(first_line, "").replace(
        "9 ledger lines omitted — over context budget", "")


def test_write_memo_qa_mode_single_section(tmp_path):
    con = _con(tmp_path)
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
               sections=[MemoSection(slug="qa", title="Question and answer",
                                     status="status_code",
                                     status_note="NOT_DISCLOSED")])
    llm = FakeLLM(parsed=[memo])
    st = _state(con, llm=llm, question="What was revenue?")
    st["session_id"] = "S-test"

    st = nodes.write_memo(st)

    prompt = llm.parse_calls[0]["prompt"]
    assert "qa" in prompt
    assert "working_capital" not in prompt


def test_dispatch_get_fact_history_filters_and_builds_ledger(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fOld", value=90.0, filed=date(2023, 5, 1),
          accn="a-1")
    _fact(con, fact_id="fNew", value=95.0, filed=date(2023, 8, 1),
          accn="a-2")   # filed after as_of below — must not appear
    payload, entries = dispatch_tool(
        con, "get_fact_history",
        {"cik": 1, "canonical_field": "revenue", "period_end": "2023-03-31",
         "period_start": "2023-01-01", "period_type": "duration",
         "unit": "USD"},
        as_of=date(2023, 6, 1), embedder=FakeEmbedder(), retrieval_k=4)
    assert "fOld" in payload and "fNew" not in payload
    assert [e.identifier for e in entries] == ["fOld"]
    assert all(e.kind == "fact" for e in entries)


def test_emit_persists_session_and_dated_conclusions(tmp_path):
    con = _con(tmp_path)
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      claims=[Claim(text="Revenue was 100",
                                                    citations=["fA"])])],
                config_version="v1+deadbeef", trace_id="tr",
                session_id="S1")
    st = _state(con, memo=memo)
    st["out_dir"] = tmp_path / "memos"
    nodes.emit(st)
    row = con.execute("SELECT cik, as_of_date FROM session").fetchone()
    assert row == (1, date(2023, 6, 1))
    c = con.execute("SELECT conclusion, learned_as_of "
                    "FROM session_conclusion").fetchone()
    assert c == ("Revenue was 100", date(2023, 6, 1))
    assert list((tmp_path / "memos").glob("*.md"))


def test_emit_writes_latency_s_when_t0_present(tmp_path):
    """F4: run_agent stashes t0 = time.monotonic() in state before the
    graph invoke; emit must turn that into a latency_s field in the
    memo JSON. Absent t0 (e.g. a node test that never sets it), the key
    is omitted rather than crashing."""
    import json
    import time

    con = _con(tmp_path)
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[], config_version="v1+deadbeef", trace_id="tr",
                session_id="S1")
    st = _state(con, memo=memo)
    st["out_dir"] = tmp_path / "memos"
    st["t0"] = time.monotonic()
    nodes.emit(st)
    stem = f"{memo.cik}_{memo.as_of}_{memo.config_version}"
    blob = json.loads((st["out_dir"] / f"{stem}.json").read_text())
    assert "latency_s" in blob
    assert blob["latency_s"] >= 0.0


def test_plan_node_full_memo_by_default():
    st = {"question": None}
    nodes.plan_node(st)
    assert len(st["plan"]) == 11


def test_plan_node_section_subset():
    """Scoped-down demonstration memos: run a named subset of sections
    instead of the full 11-section build."""
    st = {"question": None, "sections": ["business", "profitability"]}
    nodes.plan_node(st)
    assert st["plan"] == ["business", "profitability"]


def test_plan_node_rejects_unknown_section():
    import pytest
    st = {"question": None, "sections": ["business", "nonsense"]}
    with pytest.raises(ValueError, match="nonsense"):
        nodes.plan_node(st)


def test_plan_node_question_overrides_sections():
    st = {"question": "What was revenue?", "sections": ["business"]}
    nodes.plan_node(st)
    assert st["plan"] == ["qa"]
