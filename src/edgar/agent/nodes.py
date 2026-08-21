import json
import time
import uuid
from pathlib import Path

from edgar.agent.guardrails import check_memo, repair_memo
from edgar.agent.ledger import LedgerEntry
from edgar.agent.memo import Memo, render_markdown
from edgar.agent.tool_defs import TOOL_DEFS, dispatch_tool
from edgar.memory.episodic import (
    recall_conclusions, record_conclusions, save_session)
from edgar.memory.procedural import SECTIONS, load_rubric, load_system_prompt
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
    system = load_system_prompt() + \
        f"\n\nCompany CIK {state['cik']}, as_of {state['as_of']}."
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


def _truncate_ledger(rendered: str, budget: int) -> str:
    """Truncate on whole lines only (never mid-line), keeping as many
    complete lines as fit in `budget` chars. When lines are dropped, append
    a final line recording how many, so the writer and any downstream
    reader know the ledger was cut."""
    if len(rendered) <= budget:
        return rendered
    lines = rendered.split("\n")
    kept: list[str] = []
    used = 0
    for line in lines:
        added = len(line) + (1 if kept else 0)   # account for the join "\n"
        if used + added > budget:
            break
        used += added
        kept.append(line)
    omitted = len(lines) - len(kept)
    kept.append(f"[{omitted} ledger lines omitted — over context budget]")
    return "\n".join(kept)


def write_memo(state: dict) -> dict:
    cfg = state["config"]
    if state["question"]:
        section_list = "1. qa: Question and answer"
    else:
        section_list = "\n".join(f"{n}. {slug}: {title}"
                                 for n, slug, title in SECTIONS)
    ledger_text = _truncate_ledger(state["ledger"].render(),
                                   cfg.context_budget_chars)
    prompt = (
        "Write the diligence memo as structured output.\n"
        "Sections (use these slugs/titles, in order):\n" + section_list +
        "\n\nRULES: cite ONLY identifiers that appear in [brackets] in the "
        "evidence ledger below, copied verbatim. One assertion per claim. "
        "A section whose evidence is only status codes gets "
        "status='status_code' and a status_note. Value-creation ideas go "
        "in hypotheses, labeled.\n\nEVIDENCE LEDGER:\n" + ledger_text)
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
    blob = {
        "memo": memo.model_dump(mode="json"),
        "guardrail_rejections": state["guardrail_report"].rejection_count
        if state["guardrail_report"] else 0,
        "usage": state["usage"]}
    t0 = state.get("t0")
    if t0 is not None:
        blob["latency_s"] = round(time.monotonic() - t0, 3)
    (out_dir / f"{stem}.json").write_text(json.dumps(blob))
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
