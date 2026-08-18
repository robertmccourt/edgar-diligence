from dataclasses import dataclass
import duckdb


@dataclass(frozen=True)
class QualityResult:
    name: str
    passed: bool
    observed: float
    threshold: float
    detail: str


# (name, violating-row SQL, max allowed fraction, rationale)
_CHECKS: tuple[tuple[str, str, float, str], ...] = (
    ("filed_after_period",
     "SELECT count(*) FROM fact WHERE filed_date < period_end",
     0.0,
     "A fact cannot be filed before the period it describes ends. "
     "Any violation means filed_date or period_end is wrong, which would "
     "silently break every as-of query."),
    ("duration_has_start",
     "SELECT count(*) FROM fact WHERE period_type='duration' "
     "AND period_start IS NULL",
     0.0,
     "Duration facts must have a start date, or period length is unknown."),
    ("instant_has_no_start",
     "SELECT count(*) FROM fact WHERE period_type='instant' "
     "AND period_start IS NOT NULL",
     0.0,
     "Instant facts describe a point in time and must not carry a start."),
    ("mapping_rule_present",
     "SELECT count(*) FROM fact WHERE mapping_rule_id IS NULL "
     "OR mapping_rule_id = ''",
     0.0,
     "Every fact must trace to the rule that produced it (spec §5 lineage)."),
    ("value_not_null",
     "SELECT count(*) FROM fact WHERE value IS NULL",
     0.0,
     "Null values must be absent rows, not null facts, so the coverage "
     "map can classify them (spec §4.6)."),
)


def run_quality_checks(con: duckdb.DuckDBPyConnection) -> list[QualityResult]:
    total = con.execute("SELECT count(*) FROM fact").fetchone()[0] or 1
    results = []
    for name, sql, threshold, rationale in _CHECKS:
        violations = con.execute(sql).fetchone()[0]
        observed = violations / total
        results.append(QualityResult(
            name=name,
            passed=observed <= threshold,
            observed=observed,
            threshold=threshold,
            detail=f"{violations} violating rows of {total}. {rationale}",
        ))
    return results
