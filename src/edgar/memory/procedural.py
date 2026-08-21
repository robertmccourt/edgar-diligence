from pathlib import Path

SECTIONS: tuple[tuple[int, str, str], ...] = (
    (1, "business", "Business description"),
    (2, "growth", "Growth"),
    (3, "profitability", "Profitability"),
    (4, "cash_quality", "Cash quality"),
    (5, "capital_intensity", "Capital intensity"),
    (6, "working_capital", "Working capital"),
    (7, "leverage", "Balance sheet and leverage"),
    (8, "peers", "Peer positioning"),
    (9, "management", "Management's explanation and new risk factors"),
    (10, "reliability", "Data reliability flags"),
    (11, "unanswered", "What could not be answered"),
)

_DEFAULT = Path("prompts")


def load_system_prompt(prompts_dir: Path | None = None) -> str:
    return ((prompts_dir or _DEFAULT) / "system.md").read_text()


def load_rubric(slug: str, prompts_dir: Path | None = None) -> str:
    if slug not in {s[1] for s in SECTIONS}:
        raise KeyError(f"unknown section slug: {slug}")
    return ((prompts_dir or _DEFAULT) / "sections" / f"{slug}.md").read_text()
