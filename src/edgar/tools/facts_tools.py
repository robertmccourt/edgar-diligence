from datetime import date

import duckdb

from edgar.curate.mapping import CANONICAL_FIELDS
from edgar.query.asof import get_facts_asof, restatement_history
from edgar.query.coverage import coverage_map
from edgar.tools.schemas import (
    CoverageEntry, CoverageReport, FactDTO, GetFactsResult, MissingField)


def _validate_fields(fields: list[str]) -> None:
    unknown = [f for f in fields if f not in CANONICAL_FIELDS]
    if unknown:
        raise ValueError(f"unknown canonical field(s): {unknown}; "
                         f"valid: {list(CANONICAL_FIELDS)}")


def get_facts(con: duckdb.DuckDBPyConnection, cik: int, fields: list[str],
              period_start: date, period_end: date,
              as_of: date) -> GetFactsResult:
    """Spec §6: missing values return a §4.6 status, never silence.

    The status for a missing field is classified at the latest period_end
    the company has EVER filed inside the window (visible or not — a
    later-filed row is exactly what NOT_YET_FILED must detect). With no
    period at all in the window, the field is NOT_DISCLOSED.
    """
    _validate_fields(fields)
    facts = [FactDTO(fact_id=a.fact_id, cik=a.cik,
                     canonical_field=a.canonical_field,
                     value=a.value, unit=a.unit, period_type=a.period_type,
                     period_start=a.period_start, period_end=a.period_end,
                     filed_date=a.filed_date, accession=a.accession,
                     source_tag=a.source_tag)
             for a in get_facts_asof(con, cik, fields, period_start,
                                     period_end, as_of)]
    present = {f.canonical_field for f in facts}
    missing_fields = [f for f in fields if f not in present]
    missing: list[MissingField] = []
    if missing_fields:
        ref = con.execute(
            "SELECT max(period_end) FROM fact WHERE cik = ? "
            "AND period_end BETWEEN ? AND ?",
            [cik, period_start, period_end]).fetchone()[0]
        if ref is None:
            missing = [MissingField(canonical_field=f, status="NOT_DISCLOSED")
                       for f in missing_fields]
        else:
            statuses = coverage_map(con, cik, ref, as_of)
            missing = [MissingField(canonical_field=f, status=str(statuses[f]))
                       for f in missing_fields]
    return GetFactsResult(facts=facts, missing=missing)


def get_fact_history(con: duckdb.DuckDBPyConnection, cik: int, field: str,
                     period_end: date, as_of: date, *,
                     period_start: date | None, period_type: str,
                     unit: str) -> list[FactDTO]:
    """Every filed version of one figure (spec §10's restatement trail),
    capped to what was knowable on `as_of`.

    `restatement_history` returns every version regardless of when it was
    filed — the point-in-time cap is applied HERE, not there, so this is
    the only place a caller can accidentally see a future-filed version.
    """
    _validate_fields([field])
    rows = restatement_history(con, cik, field, period_end,
                               period_start=period_start,
                               period_type=period_type, unit=unit)
    return [FactDTO(fact_id=a.fact_id, cik=a.cik,
                    canonical_field=a.canonical_field, value=a.value,
                    unit=a.unit, period_type=a.period_type,
                    period_start=a.period_start, period_end=a.period_end,
                    filed_date=a.filed_date, accession=a.accession,
                    source_tag=a.source_tag)
            for a in rows if a.filed_date <= as_of]


def list_available_facts(con: duckdb.DuckDBPyConnection, cik: int,
                         as_of: date,
                         max_periods: int = 24) -> CoverageReport:
    """Spec §6: the coverage map that makes refusal reachable."""
    period_ends = [r[0] for r in con.execute(
        "SELECT DISTINCT period_end FROM fact WHERE cik = ? "
        "AND filed_date <= ? ORDER BY period_end DESC LIMIT ?",
        [cik, as_of, max_periods]).fetchall()]
    entries = [CoverageEntry(
                   period_end=pe,
                   statuses={k: str(v) for k, v in
                             coverage_map(con, cik, pe, as_of).items()})
               for pe in period_ends]
    return CoverageReport(cik=cik, as_of=as_of, entries=entries)
