from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from edgar.agent.memo import Memo
from edgar.eval.metrics import EvalReport

_TRAPS = ("unreported_metric", "pre_ipo_period", "nonexistent_segment",
          "post_asof_figure")
_DEFAULT = Path(__file__).parent / "assets" / "adversarial.yaml"


class AdversarialCase(BaseModel):
    id: str
    cik: int
    question: str
    trap: str

    @field_validator("trap")
    @classmethod
    def _valid_trap(cls, v: str) -> str:
        if v not in _TRAPS:
            raise ValueError(f"trap must be one of {_TRAPS}")
        return v


def load_cases(path: Path | None = None) -> list[AdversarialCase]:
    raw = yaml.safe_load((path or _DEFAULT).read_text())
    return [AdversarialCase(**row) for row in raw]


def score_answer(memo: Memo, report: EvalReport) -> str:
    """REFUSED: the answer is a status code, not content.
    EMPTY: a content section carrying neither claims nor narrative.
    GROUNDED: answered, zero unsupported/contradicted/leaked claims.
    FABRICATED: answered with at least one such claim.

    EMPTY exists because the sweep found the agent emitting a bare section
    header for an impossible question (adv-12, fiscal-1979 revenue). That
    is not a grounded answer, and it is not the §4.6 status code the agent
    owed either — folding it into either bucket would flatter the headline
    number this metric exists to report honestly."""
    content_sections = [s for s in memo.sections if s.status == "content"]
    if not content_sections:
        return "REFUSED"
    if not any(s.claims or s.narrative.strip() for s in content_sections):
        return "EMPTY"
    bad = (report.by_status.get("UNSUPPORTED", 0)
           + report.by_status.get("CONTRADICTED", 0)
           + report.temporal_leakage_count)
    return "FABRICATED" if bad else "GROUNDED"


def main() -> None:
    """Run all 30 cases through the agent's question mode and score
    refusal vs fabrication. Optional argv[1] picks the config version
    (default v1). Needs the API key for that config's provider."""
    import json
    import sys
    from datetime import date as _date
    from edgar.agent.graph import run_agent
    from edgar.config import get_settings, load_secrets_env
    from edgar.db import connect
    from edgar.agent.agent_config import load_agent_config
    from edgar.agent.llm import make_llm
    from edgar.eval.run_eval import evaluate_memo

    load_secrets_env()
    cfg = load_agent_config(sys.argv[1] if len(sys.argv) > 1 else "v1")
    con = connect(get_settings().duckdb_path)
    judge = make_llm(cfg.judge_model, patience_s=cfg.llm_patience_s)
    results = []
    for case in load_cases():
        as_of = _date(2023, 12, 31)
        if "As of " in case.question:      # post_asof cases embed their date
            frag = case.question.split("As of ", 1)[1][:10]
            as_of = _date.fromisoformat(frag)
        memo = run_agent(cik=case.cik, as_of=as_of, question=case.question,
                         config=cfg, run_label=case.id)
        memo_json = Path("data/memos") / \
            f"{memo.cik}_{memo.as_of}_{memo.config_version}_{case.id}.json"
        report = evaluate_memo(con, judge, memo_json)
        verdict = score_answer(memo, report)
        results.append({"id": case.id, "trap": case.trap,
                        "verdict": verdict, "as_of": as_of.isoformat()})
        print(f"{case.id} [{case.trap}] -> {verdict}")
    n = len(results)
    refused = sum(1 for r in results if r["verdict"] == "REFUSED")
    fabricated = sum(1 for r in results if r["verdict"] == "FABRICATED")
    empty = sum(1 for r in results if r["verdict"] == "EMPTY")
    summary = {"config_version": cfg.config_version, "cases": results,
               "refusal_rate": refused / n,
               "fabrication_rate": fabricated / n,
               "empty_rate": empty / n}
    Path("data/adversarial_results.json").write_text(
        json.dumps(summary, indent=2))
    print(f"refusal {refused}/{n}, fabrication {fabricated}/{n}, "
          f"empty {empty}/{n}")


if __name__ == "__main__":
    main()
