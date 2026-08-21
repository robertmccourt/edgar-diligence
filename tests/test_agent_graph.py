from datetime import date

from edgar.db import connect, init_schema
from edgar.curate.facts import create_fact_table
from edgar.curate.mapping import create_mapping_table
from edgar.narrative.store import create_narrative_tables
from edgar.memory.episodic import create_memory_tables
from edgar.tools.compute import create_derivation_table
from edgar.agent.graph import run_agent
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall
from edgar.agent.memo import Claim, Memo, MemoSection
from edgar.narrative.embedder import FakeEmbedder


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


def test_full_run_with_fakes_produces_cited_memo(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
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
                    embedder=FakeEmbedder(), out_dir=tmp_path / "memos")
    assert out.sections[0].claims[0].citations == ["fA"]
    assert out.session_id and out.trace_id and out.config_version
