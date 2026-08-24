"""Deterministic claim extraction from a rendered memo (spec §8.0).

Extraction parses; it does not infer. The memo is a structured artifact
this system emits — one assertion per bullet, citation ids in [brackets],
hypotheses under a labeled heading — so a regex recovers the claims
exactly, every run. The rendered markdown (not the Memo object) is the
input on purpose: the eval judges what a reader sees, including anything
lost in rendering.

Values are normalised to base units here rather than guessed at scoring
time. The blind scale trial this replaces passed a 3.1-point margin
*change* as a 30.7% margin *level* (pilot claim 14, 2026-08-23).
"""
import re
from typing import Callable

from edgar.eval.schemas import RawClaim

_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_BULLET = re.compile(r"^-\s+(.*?)\s*$")
_CITE = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9\-]*)\]")
_HYP_PREFIX = re.compile(r"^\*\*Hypothesis:\*\*\s*")
_HYP_HEADING = "value-creation hypotheses"

# §4.6 missing-value taxonomy. A bullet reporting one of these is the memo
# obeying the spec's "report absence, never drop it" rule, not an uncited
# assertion — the same reasoning that excludes labeled hypotheses (§8.2).
_STATUS_CODE = re.compile(
    r"\b(NOT_DISCLOSED|NOT_APPLICABLE|NOT_YET_FILED|UNMAPPED|AMBIGUOUS)\b")

_NUM = r"(-?\d[\d,]*(?:\.\d+)?)"
_MAG = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6,
        "mn": 1e6, "thousand": 1e3}
# Only numbers carrying an explicit unit are candidates. Dates ("December
# 31, 2023", "2024-03-01") carry none, so they are excluded structurally
# rather than by pattern-matching every date format.
_MONEY = re.compile(r"\$\s*" + _NUM +
                    r"(?:\s*(trillion|billion|bn|million|mn|thousand))?",
                    re.I)
_MAGNITUDE = re.compile(_NUM +
                        r"\s*(trillion|billion|bn|million|mn|thousand)\b",
                        re.I)
_PERCENT = re.compile(_NUM + r"\s*(?:%|percent\b)", re.I)
_BPS = re.compile(_NUM + r"\s*(?:bps|basis points)\b", re.I)
_MULTIPLE = re.compile(_NUM + r"\s*[x×]\b", re.I)


def _f(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse_values_verbose(text: str) -> list[tuple[float, str]]:
    """Every unit-carrying number in `text` as (normalised value, as
    written), ordered by position. Percentages and basis points become
    fractions; magnitude words are multiplied out; bare integers — dates,
    counts — are ignored, which is what keeps "December 31, 2023" out of
    the claimed values."""
    found: list[tuple[int, float, str]] = []
    for m in _PERCENT.finditer(text):
        found.append((m.start(), _f(m.group(1)) / 100.0, m.group(0)))
    for m in _BPS.finditer(text):
        found.append((m.start(), _f(m.group(1)) / 10_000.0, m.group(0)))
    for m in _MULTIPLE.finditer(text):
        found.append((m.start(), _f(m.group(1)), m.group(0)))
    for pattern in (_MONEY, _MAGNITUDE):
        for m in pattern.finditer(text):
            if any(abs(m.start() - pos) < 3 for pos, _, _ in found):
                continue          # same number already captured
            scale = _MAG.get((m.group(2) or "").lower(), 1.0)
            found.append((m.start(), _f(m.group(1)) * scale, m.group(0)))
    seen: set[int] = set()
    out: list[tuple[float, str]] = []
    for pos, val, raw in sorted(found):
        if pos in seen:
            continue
        seen.add(pos)
        out.append((val, raw.strip()))
    return out


def parse_values(text: str) -> list[float]:
    return [v for v, _ in parse_values_verbose(text)]


def _classify(citations: list[str], values: list[float],
              id_kind: Callable[[str], str], is_hypothesis: bool) -> str:
    if is_hypothesis:
        return "INFERENTIAL"
    if not citations:
        return "UNSUPPORTED"
    kinds = [id_kind(c) for c in citations]
    levels = [k for k in kinds if k in ("fact", "derivation")]
    # Two cited levels and two stated numbers is a change described from
    # its endpoints — not a computed quantity needing a derivation_id.
    if len(levels) >= 2 and len(values) >= 2:
        return "COMPARATIVE"
    if "derivation" in kinds:
        return "DERIVED"
    if "span" in kinds:
        return "ATTRIBUTED"
    if "fact" in kinds:
        return "NUMERIC" if values else "INFERENTIAL"
    return "INFERENTIAL"


def _bullets(markdown: str):
    """Yield (section_title, bullet_text, is_hypothesis) in document
    order."""
    section = ""
    in_hypotheses = False
    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(1)
            in_hypotheses = section.strip().lower() == _HYP_HEADING
            continue
        bullet = _BULLET.match(line)
        if bullet:
            yield section, bullet.group(1), in_hypotheses


def extract_claims(markdown: str, id_kind: Callable[[str], str], *,
                   keep_status_reports: bool = False) -> list[RawClaim]:
    claims: list[RawClaim] = []
    for section, body, is_hyp in _bullets(markdown):
        citations = _CITE.findall(body)
        text = _HYP_PREFIX.sub("", _CITE.sub("", body)).strip()
        text = re.sub(r"\s{2,}", " ", text)
        values = parse_values(text)
        is_status = bool(_STATUS_CODE.search(text)) and not citations
        if is_status and not keep_status_reports:
            continue
        claims.append(RawClaim(
            is_status_report=is_status,
            claim_text=text,
            claim_type=_classify(citations, values, id_kind, is_hyp),
            citations=citations,
            claimed_value=values[0] if values else None,
            claimed_values=values,
            is_hypothesis=is_hyp,
            section=section))
    return claims


def narrative_gaps(markdown: str, id_kind: Callable[[str], str],
                   tolerance: float = 0.02) -> list[str]:
    """Figures stated in a section's narrative prose but backed by none of
    that section's cited bullets. Deterministic: the check is numeric, so
    no model opinion is involved."""
    per_section: dict[str, list[float]] = {}
    for claim in extract_claims(markdown, id_kind):
        if claim.citations:
            per_section.setdefault(claim.section, []).extend(
                claim.claimed_values)

    problems: list[str] = []
    section = ""
    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(1)
            continue
        if not line.strip() or line.startswith(("-", "#", "*", "_", "|")):
            continue
        if section.strip().lower() == _HYP_HEADING:
            continue
        backed = per_section.get(section, [])
        for value, as_written in parse_values_verbose(line):
            if any(abs(value - b) <= tolerance * max(abs(b), 1e-9)
                   for b in backed):
                continue
            problems.append(
                f"{section}: narrative states {as_written} but no cited "
                f"bullet in that section carries it")
    return problems
