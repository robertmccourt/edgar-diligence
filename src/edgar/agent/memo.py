from datetime import date

from pydantic import BaseModel


class Claim(BaseModel):
    text: str
    citations: list[str] = []
    is_hypothesis: bool = False


class MemoSection(BaseModel):
    slug: str
    title: str
    status: str = "content"
    narrative: str = ""
    claims: list[Claim] = []
    status_note: str = ""


class Memo(BaseModel):
    cik: int
    company_name: str
    as_of: date
    sections: list[MemoSection]
    hypotheses: list[Claim] = []
    config_version: str = ""
    trace_id: str = ""
    session_id: str = ""


def render_markdown(memo: Memo) -> str:
    lines = [f"# Diligence memo: {memo.company_name} (CIK {memo.cik})",
             f"*As of {memo.as_of.isoformat()} — only information filed on "
             f"or before this date was used.*",
             f"*config {memo.config_version} · trace {memo.trace_id}*", ""]
    for s in memo.sections:
        lines.append(f"## {s.title}")
        if s.status == "status_code":
            lines += [f"_Not producible from the store:_ {s.status_note}",
                      ""]
            continue
        if s.narrative:
            lines += [s.narrative, ""]
        for c in s.claims:
            cites = " ".join(f"[{i}]" for i in c.citations)
            lines.append(f"- {c.text} {cites}".rstrip())
        lines.append("")
    if memo.hypotheses:
        lines.append("## Value-creation hypotheses")
        lines.append("_Hypothesis, not established by the data "
                     "(spec §7.7)._")
        for c in memo.hypotheses:
            cites = " ".join(f"[{i}]" for i in c.citations)
            lines.append(f"- **Hypothesis:** {c.text} {cites}".rstrip())
        lines.append("")
    return "\n".join(lines)
