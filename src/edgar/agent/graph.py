from datetime import date
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from edgar.agent import nodes
from edgar.agent.agent_config import load_agent_config
from edgar.agent.ledger import EvidenceLedger
from edgar.agent.memo import Memo


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
              config=None, llm=None, embedder=None, tracer=None,
              con=None, out_dir: Path = Path("data/memos")) -> Memo:
    from edgar.ops.tracing import make_tracer
    cfg = config or load_agent_config("v1")
    if con is None:
        from edgar.db import connect
        con = connect()
    if llm is None:
        from edgar.agent.llm import AnthropicLLM
        llm = AnthropicLLM(cfg.generation_model)
    if embedder is None:
        from edgar.narrative.embedder import SentenceTransformerEmbedder
        embedder = SentenceTransformerEmbedder()
    tracer = tracer or make_tracer(f"memo-{cik}-{as_of}")
    row = con.execute("SELECT name FROM company WHERE cik = ?",
                      [cik]).fetchone()
    state = dict(cik=cik, as_of=as_of, question=question, config=cfg,
                 con=con, llm=llm, embedder=embedder, tracer=tracer,
                 ledger=EvidenceLedger(), coverage=None, plan=[],
                 section_idx=0, memo=None, guardrail_report=None,
                 repair_round=0, conclusions=[], recalled_ids=[],
                 usage={"in": 0, "out": 0},
                 company_name=row[0] if row else f"CIK {cik}",
                 out_dir=out_dir)
    final = build_graph().invoke(state)
    return final["memo"]
