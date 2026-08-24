import time
from datetime import date
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from edgar.agent import nodes
from edgar.agent.agent_config import load_agent_config
from edgar.agent.ledger import EvidenceLedger
from edgar.agent.memo import Memo
from edgar.memory.episodic import create_memory_tables
from edgar.narrative.store import create_narrative_tables
from edgar.tools.compute import create_derivation_table


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


def run_agent(*, cik: int, as_of: date, question: str | None = None,
              sections: list[str] | None = None,
              config=None, llm=None, embedder=None, tracer=None,
              con=None, out_dir: Path = Path("data/memos")) -> Memo:
    from edgar.ops.tracing import make_tracer
    cfg = config or load_agent_config("v1")
    if con is None:
        from edgar.db import connect
        con = connect()
    # Real store may predate these tables (mirrors the 50fa43b index_spans
    # hardening) — ensure them here so `make memo` against a fresh/older
    # store doesn't crash on the first session-memory or compute call.
    create_derivation_table(con)
    create_memory_tables(con)
    create_narrative_tables(con)
    if llm is None:
        from edgar.agent.llm import make_llm
        llm = make_llm(cfg.generation_model,
                       patience_s=cfg.llm_patience_s)
    if embedder is None:
        from edgar.narrative.embedder import SentenceTransformerEmbedder
        embedder = SentenceTransformerEmbedder()
    tracer = tracer or make_tracer(f"memo-{cik}-{as_of}")
    row = con.execute("SELECT name FROM company WHERE cik = ?",
                      [cik]).fetchone()
    state = dict(cik=cik, as_of=as_of, question=question,
                 sections=sections, config=cfg,
                 con=con, llm=llm, embedder=embedder, tracer=tracer,
                 ledger=EvidenceLedger(), coverage=None, plan=[],
                 section_idx=0, memo=None, guardrail_report=None,
                 repair_round=0, conclusions=[], recalled_ids=[],
                 usage={"in": 0, "out": 0},
                 company_name=row[0] if row else f"CIK {cik}",
                 out_dir=out_dir, t0=time.monotonic())
    final = build_graph().invoke(state)
    return final["memo"]
