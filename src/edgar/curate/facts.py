import hashlib
import duckdb
from edgar.curate.periods import parse_period, parse_yyyymmdd

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

    Rows whose tag has no rule are skipped — they are the mapping tail,
    surfaced by the coverage report rather than silently dropped.

    Competing tags are arbitrated by `mapping_rule.priority`. Several tags
    map to the same canonical field (NetIncomeLoss and ProfitLoss both mean
    net_income; four tags mean revenue) and a single filing routinely
    reports more than one of them for the same period. Emitting a fact for
    each makes the figure ambiguous at query time: measured on the 2024
    Q1-Q2 smoke build, 16,228 (cik, field, period, accession) groups carried
    more than one distinct value from a single filing. So within one
    (adsh, ddate, qtrs, uom, coreg) — one filing, one period, one unit — the
    lowest-priority rule wins per canonical field and the losing tags emit
    nothing. A filing reporting both NetIncomeLoss and ProfitLoss yields
    exactly one net_income fact, from NetIncomeLoss, which is what the
    MR-0011 rationale ("used when NetIncomeLoss absent") has always claimed.

    The same priority mechanism decides net_income, stockholders_equity, and
    capex whenever a filing reports both a parent-only figure and a
    noncontrolling-interest-inclusive or broader variant: the higher-priority
    (parent-only / narrower) tag wins and the losing tag's value is not
    stored anywhere in the database — not as a fact, not elsewhere. This is
    the intended convention, not a gap.

    This is the structural tier of resolution and uses only `num.txt`. The
    richer refinement — preferring the tag presented in the primary
    statement per `pre.txt` — is deliberately not attempted here.

    Ties are broken on source_tag, then value, then source_quarter, so the
    same input always produces the same fact regardless of scan order.
    `uom` is part of the arbitration key, never collapsed: a company filing
    the same balance-sheet line in USD and CNY has filed two figures, and
    each is arbitrated on its own.

    Rows whose filing date precedes the end of the period they describe are
    dropped as impossible: a company cannot report actuals for a period that
    has not ended. (Observed in the source data as e.g. a balance sheet dated
    2024-12-31 filed 2024-05-07.) Admitting them would make the
    filed_after_period quality check unsatisfiable at its 0.0 threshold, and
    relaxing that threshold to tolerate contradictory dates would forfeit the
    only guard the as-of query has against a corrupted timeline.
    """
    rows = con.execute(
        """
        WITH best AS (
            SELECT source_tag, canonical_field, sign_convention, scale,
                   mapping_rule_id, confidence, priority,
                   row_number() OVER (
                       PARTITION BY source_tag, canonical_field
                       ORDER BY priority, mapping_rule_id) AS rn
            FROM mapping_rule
        ),
        mapped AS (
            SELECT n.adsh AS adsh, n.tag AS tag, n.ddate AS ddate,
                   n.qtrs AS qtrs, n.uom AS uom,
                   coalesce(n.coreg, '') AS coreg,
                   n.value AS value, s.cik AS cik, s.filed AS filed,
                   s.fy AS fy, s.fp AS fp,
                   n.source_quarter AS source_quarter,
                   b.canonical_field AS canonical_field,
                   b.sign_convention AS sign_convention, b.scale AS scale,
                   b.mapping_rule_id AS mapping_rule_id,
                   b.confidence AS confidence, b.priority AS priority
            FROM raw_num n
            JOIN raw_sub s
              ON s.adsh = n.adsh AND s.source_quarter = n.source_quarter
            JOIN best b ON b.source_tag = n.tag AND b.rn = 1
            WHERE n.value IS NOT NULL AND trim(n.value) <> ''
              AND coalesce(n.coreg, '') = ''
              AND coalesce(n.segments, '') = ''
        ),
        arbitrated AS (
            SELECT *, row_number() OVER (
                       PARTITION BY adsh, ddate, qtrs, uom, coreg,
                                    canonical_field
                       ORDER BY priority, tag, value, source_quarter) AS pick
            FROM mapped
        )
        SELECT adsh, tag, ddate, qtrs, uom, coreg,
               value, cik, filed, fy, fp, source_quarter,
               canonical_field, sign_convention, scale,
               mapping_rule_id, confidence
        FROM arbitrated
        WHERE pick = 1
        """
    ).fetchall()

    payload = []
    malformed = 0
    impossible = 0
    for (adsh, tag, ddate, qtrs, uom, coreg, value, cik, filed, fy, fp,
         src_q, field, sign, scale, rule_id, conf) in rows:
        try:
            period = parse_period(ddate, qtrs)
            numeric = float(value) * sign * scale
            filed_date = parse_yyyymmdd(filed)
        except (ValueError, TypeError):
            malformed += 1
            continue
        if filed_date < period.end:
            impossible += 1
            continue
        payload.append((
            make_fact_id(adsh, tag, ddate, qtrs, uom, coreg),
            int(cik), field, numeric, uom, str(period.period_type),
            period.start, period.end, fy, fp, filed_date, adsh, tag,
            rule_id, conf, src_q,
        ))

    if payload:
        con.executemany(
            "INSERT OR REPLACE INTO fact VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            payload,
        )

    attempted = len(payload)
    persisted = len({row[0] for row in payload})
    if persisted != attempted:
        print(
            f"WARNING: build_facts: {attempted} rows attempted, "
            f"{persisted} persisted, {attempted - persisted} dropped by "
            f"fact_id collision"
        )
    skipped = malformed + impossible
    if skipped:
        print(
            f"WARNING: build_facts: {skipped} of {len(rows)} rows skipped "
            f"({malformed} malformed ddate, qtrs, value, or filed; "
            f"{impossible} filed before the period they describe ended)"
        )
    return persisted
