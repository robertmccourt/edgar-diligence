from pydantic import BaseModel

from edgar.eval.schemas import Verdict


class EvalReport(BaseModel):
    memo_path: str
    config_version: str
    session_id: str
    as_of: str
    n_claims: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    unsupported_rate: float
    contradiction_rate: float
    citation_coverage: float
    temporal_leakage_count: int
    temporal_problems: list[str]
    guardrail_rejections: int


def compute_metrics(verdicts: list[Verdict],
                    temporal_problems: list[str], *,
                    memo_meta: dict) -> EvalReport:
    n = len(verdicts)
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for v in verdicts:
        by_type[v.claim.claim_type] = by_type.get(v.claim.claim_type, 0) + 1
        by_status[v.status] = by_status.get(v.status, 0) + 1
    unsupported = by_status.get("UNSUPPORTED", 0)
    contradicted = by_status.get("CONTRADICTED", 0)
    cited = sum(1 for v in verdicts if v.claim.citations)
    return EvalReport(
        memo_path=memo_meta["memo_path"],
        config_version=memo_meta["config_version"],
        session_id=memo_meta["session_id"],
        as_of=memo_meta["as_of"],
        n_claims=n,
        by_type=by_type,
        by_status=by_status,
        unsupported_rate=unsupported / n if n else 0.0,
        contradiction_rate=contradicted / n if n else 0.0,
        citation_coverage=cited / n if n else 0.0,
        temporal_leakage_count=len(temporal_problems),
        temporal_problems=temporal_problems,
        guardrail_rejections=memo_meta.get("guardrail_rejections", 0))


def to_markdown(report: EvalReport) -> str:
    lines = [
        "# Groundedness evaluation",
        f"Memo: `{report.memo_path}` · config `{report.config_version}` · "
        f"session `{report.session_id}` · as_of {report.as_of}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Claims | {report.n_claims} |",
        f"| Unsupported-claim rate | {report.unsupported_rate:.1%} |",
        f"| Contradiction rate | {report.contradiction_rate:.1%} |",
        f"| Citation coverage | {report.citation_coverage:.1%} |",
        f"| Temporal leakage count | {report.temporal_leakage_count} |",
        f"| Guardrail rejections | {report.guardrail_rejections} |",
        "",
        "By type: " + ", ".join(f"{k}={v}" for k, v in
                                sorted(report.by_type.items())),
        "By status: " + ", ".join(f"{k}={v}" for k, v in
                                  sorted(report.by_status.items())),
    ]
    if report.temporal_problems:
        lines += ["", "## Temporal problems (MUST be zero)"]
        lines += [f"- {p}" for p in report.temporal_problems]
    return "\n".join(lines) + "\n"
