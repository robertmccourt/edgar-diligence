import shutil
from pathlib import Path
from edgar.db import connect, init_schema
from edgar.ingest.archives import Quarter, enumerate_quarters, download_archive
from edgar.ingest.extract import extract_archive
from edgar.ingest.load import load_quarter
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.curate.company import build_company_table
from edgar.curate.facts import create_fact_table, build_facts
from edgar.curate.universe import apply_eligibility
from edgar.analysis.restatement import restatement_stats, filing_lag_stats
from edgar.quality.checks import run_quality_checks
from edgar.quality.coverage_stats import mapping_coverage


def build_all(con, start: Quarter, end: Quarter, raw_dir: Path) -> dict:
    """Full Stage 1 build. Safe to re-run: every step is idempotent."""
    init_schema(con)
    create_mapping_table(con)
    seed_mapping_rules(con)
    create_fact_table(con)

    for q in enumerate_quarters(start, end):
        zip_path = download_archive(q, raw_dir)
        extract_dir = raw_dir / q.label
        try:
            files = extract_archive(zip_path, extract_dir)
            load_quarter(con, files, q)
        finally:
            # The extracted text files are ~500MB per quarter and are fully
            # regenerable from the cached .zip. Keeping all of them would add
            # ~12GB across the 2019-2026 range and has already exhausted the
            # disk on one full build. Remove each quarter's once it is loaded.
            shutil.rmtree(extract_dir, ignore_errors=True)

    n_companies = build_company_table(con)
    n_facts = build_facts(con)
    coverage = mapping_coverage(con)
    eligibility = apply_eligibility(con)

    return {
        "companies": n_companies,
        "facts": n_facts,
        "coverage": coverage,
        "eligibility": eligibility,
        "restatement": restatement_stats(con),
        "filing_lag": filing_lag_stats(con),
        "quality": [r.__dict__ for r in run_quality_checks(con)],
    }


def rebuild_curated(con) -> dict:
    """Rebuild the curated zone from an already-loaded raw zone.

    For mapping-rule changes: full fact rebuild without re-downloading or
    re-loading 29 quarters of archives. Deletes fact rows first so facts
    from removed rules cannot linger (build_facts alone only ever adds).

    Stage-then-swap, so the live table is never in a partial state. A
    rebuild killed mid-insert used to leave an arbitrary committed prefix
    of the facts that passed every rate-based quality check — this
    happened twice on the real store (machine sleep, 2026-08), leaving
    2.6M of ~4.7M rows and a memo agent that couldn't find Apple's annual
    revenue. Inserts now go to fact_staging in per-chunk commits (a single
    ~5M-row transaction OOMs DuckDB's pool — observed at 12.7GiB), and the
    only mutation of `fact` is the transactional drop+rename at the end:
    an interruption anywhere leaves the previous complete table in place.
    """
    create_mapping_table(con)
    seed_mapping_rules(con)
    create_fact_table(con)
    con.execute("DROP TABLE IF EXISTS fact_staging")
    create_fact_table(con, "fact_staging")
    n_facts = build_facts(con, table="fact_staging")
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DROP TABLE fact")
        con.execute("ALTER TABLE fact_staging RENAME TO fact")
        n_companies = build_company_table(con)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    stored = con.execute("SELECT count(*) FROM fact").fetchone()[0]
    if stored != n_facts:
        raise RuntimeError(
            f"rebuild_curated: build_facts reported {n_facts} facts but "
            f"the table holds {stored} — partial or corrupted build")
    return {
        "companies": n_companies,
        "facts": n_facts,
        "coverage": mapping_coverage(con),
        "eligibility": apply_eligibility(con),
        "quality": [r.__dict__ for r in run_quality_checks(con)],
    }


if __name__ == "__main__":
    import json
    from edgar.config import get_settings
    s = get_settings()
    con = connect(s.duckdb_path)
    report = build_all(
        con,
        Quarter(s.start_year, s.start_quarter),
        Quarter(2026, 1),  # latest posted; 2026q2 not yet available
        s.raw_dir,
    )
    print(json.dumps(report, indent=2, default=str))
