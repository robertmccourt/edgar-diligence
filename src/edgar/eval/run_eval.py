"""Evaluate one memo: decompose -> judge -> temporal -> report.

Usage: venv/bin/python -m edgar.eval.run_eval data/memos/<stem>.json
Writes <stem>.report.json / .report.md / .verdicts.json beside the memo.
"""
import json
import sys
from datetime import date
from pathlib import Path

from edgar.agent.memo import Memo, render_markdown
from edgar.eval.decompose import decompose
from edgar.eval.judges import judge_claim, temporal_leakage
from edgar.eval.metrics import EvalReport, compute_metrics, to_markdown


def evaluate_memo(con, llm, memo_json_path: Path) -> EvalReport:
    blob = json.loads(memo_json_path.read_text())
    memo = Memo.model_validate(blob["memo"])
    as_of = memo.as_of if isinstance(memo.as_of, date) else \
        date.fromisoformat(str(memo.as_of))
    claims = decompose(llm, render_markdown(memo))
    verdicts = [judge_claim(con, llm, c, as_of) for c in claims]
    problems = temporal_leakage(con, claims, as_of,
                                session_id=memo.session_id or None)
    report = compute_metrics(verdicts, problems, memo_meta={
        "memo_path": str(memo_json_path),
        "config_version": memo.config_version,
        "session_id": memo.session_id,
        "as_of": as_of.isoformat(),
        "guardrail_rejections": blob.get("guardrail_rejections", 0)})
    stem = memo_json_path.with_suffix("")
    Path(f"{stem}.report.json").write_text(report.model_dump_json(indent=2))
    Path(f"{stem}.report.md").write_text(to_markdown(report))
    Path(f"{stem}.verdicts.json").write_text(json.dumps(
        [v.model_dump(mode="json") for v in verdicts], indent=2))
    return report


def main() -> None:
    from edgar.agent.agent_config import load_agent_config
    from edgar.agent.llm import make_llm
    from edgar.config import get_settings, load_secrets_env
    from edgar.db import connect
    from edgar.memory.episodic import create_memory_tables
    from edgar.tools.compute import create_derivation_table
    load_secrets_env()
    cfg = load_agent_config(sys.argv[2] if len(sys.argv) > 2 else "v1")
    con = connect(get_settings().duckdb_path)
    # Real store may predate these tables — temporal_leakage/recompute
    # read `session`/`derivation` when evaluating an older memo on a
    # fresh store. Idempotent CREATE IF NOT EXISTS.
    create_derivation_table(con)
    create_memory_tables(con)
    report = evaluate_memo(con, make_llm(cfg.judge_model,
                                         patience_s=cfg.llm_patience_s),
                           Path(sys.argv[1]))
    print(to_markdown(report))


if __name__ == "__main__":
    main()
