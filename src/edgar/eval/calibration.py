import csv
import json
import random
from pathlib import Path

_HEADER = ["claim_text", "claim_type", "judge_status", "judge_reason",
           "human_status"]


def sample_for_labeling(report_paths: list[Path], n: int,
                        out_csv: Path) -> int:
    """Stratified sample of judged claims into a labeling sheet.

    Reads verdict lists persisted by run_eval (*.verdicts.json). Stratifies
    across claim types round-robin so rare types are represented.
    Deterministic: seeded by sorted input paths, not wall clock.
    """
    by_type: dict[str, list[dict]] = {}
    for p in sorted(report_paths):
        for v in json.loads(Path(p).read_text()):
            by_type.setdefault(v["claim"]["claim_type"], []).append(v)
    rng = random.Random(0)
    for rows in by_type.values():
        rng.shuffle(rows)
    picked: list[dict] = []
    while len(picked) < n and any(by_type.values()):
        for t in sorted(by_type):
            if by_type[t] and len(picked) < n:
                picked.append(by_type[t].pop())
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_HEADER)
        for v in picked:
            w.writerow([v["claim"]["claim_text"],
                        v["claim"]["claim_type"],
                        v["status"], v["reason"], ""])
    return len(picked)


def cohens_kappa(labels_csv: Path) -> tuple[float, int]:
    """Two-rater kappa over rows whose human_status is filled.

    Headline number collapses to SUPPORTED vs NOT; the full-matrix kappa
    is what this returns (categories as labeled)."""
    pairs: list[tuple[str, str]] = []
    with labels_csv.open() as fh:
        for row in csv.DictReader(fh):
            human = (row.get("human_status") or "").strip()
            if human:
                pairs.append((row["judge_status"].strip(), human))
    if not pairs:
        return 0.0, 0
    cats = sorted({c for p in pairs for c in p})
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == c for a, _ in pairs) / n) *
             (sum(b == c for _, b in pairs) / n) for c in cats)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return kappa, n
