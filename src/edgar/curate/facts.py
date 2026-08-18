import hashlib
import duckdb
from edgar.curate.periods import parse_period

FACT_DDL = """
CREATE TABLE IF NOT EXISTS fact (
    fact_id VARCHAR PRIMARY KEY,
    cik BIGINT,
    canonical_field VARCHAR,
    value DOUBLE,
    unit VARCHAR,
    period_type VARCHAR,
    period_start DATE,
    period_end DATE,
    fiscal_year VARCHAR,
    fiscal_period VARCHAR,
    filed_date DATE,
    accession VARCHAR,
    source_tag VARCHAR,
    mapping_rule_id VARCHAR,
    confidence DOUBLE,
    source_quarter VARCHAR
);
"""


def make_fact_id(adsh: str, tag: str, ddate: str, qtrs: str,
                 uom: str, coreg: str) -> str:
    key = "|".join([adsh, tag, ddate, str(qtrs), uom, coreg or ""])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def create_fact_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(FACT_DDL)


def build_facts(con: duckdb.DuckDBPyConnection) -> int:
    """Project mapped raw_num rows into the bitemporal fact table.

    Only the highest-priority rule for each tag is applied. Rows whose tag
    has no rule are skipped — they are the mapping tail, surfaced by the
    coverage report rather than silently dropped.
    """
    rows = con.execute(
        """
        WITH best AS (
            SELECT source_tag, canonical_field, sign_convention, scale,
                   mapping_rule_id, confidence,
                   row_number() OVER (PARTITION BY source_tag
                                      ORDER BY priority) AS rn
            FROM mapping_rule
        )
        SELECT n.adsh, n.tag, n.ddate, n.qtrs, n.uom, coalesce(n.coreg, ''),
               n.value, s.cik, s.filed, s.fy, s.fp, n.source_quarter,
               b.canonical_field, b.sign_convention, b.scale,
               b.mapping_rule_id, b.confidence
        FROM raw_num n
        JOIN raw_sub s
          ON s.adsh = n.adsh AND s.source_quarter = n.source_quarter
        JOIN best b ON b.source_tag = n.tag AND b.rn = 1
        WHERE n.value IS NOT NULL AND trim(n.value) <> ''
          AND coalesce(n.coreg, '') = ''
        """
    ).fetchall()

    payload = []
    for (adsh, tag, ddate, qtrs, uom, coreg, value, cik, filed, fy, fp,
         src_q, field, sign, scale, rule_id, conf) in rows:
        try:
            period = parse_period(ddate, qtrs)
            numeric = float(value) * sign * scale
            filed_date = f"{filed[:4]}-{filed[4:6]}-{filed[6:]}"
        except (ValueError, TypeError):
            continue
        payload.append((
            make_fact_id(adsh, tag, ddate, qtrs, uom, coreg),
            int(cik), field, numeric, uom, str(period.period_type),
            period.start, period.end, fy, fp, filed_date, adsh, tag,
            rule_id, conf, src_q,
        ))

    con.executemany(
        "INSERT OR REPLACE INTO fact VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        payload,
    )
    return len(payload)
