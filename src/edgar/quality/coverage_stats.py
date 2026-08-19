import duckdb
from edgar.curate.mapping import CANONICAL_FIELDS


def mapping_coverage(con: duckdb.DuckDBPyConnection) -> dict:
    """Measure where raw_num rows go on their way into the fact table.

    Mirrors build_facts's WHERE clause (segments, then coreg, then a NULL
    or blank value) so each dropped_* bucket is a disjoint slice of the
    mapped rows and the funnel reconciles exactly: mapped_tag_rows minus
    the three dropped_* counts equals eligible_rows, and eligible_rows
    minus facts equals arbitrated_away.
    """
    funnel = con.execute(
        """
        WITH tagged AS (
            SELECT n.value AS value, n.coreg AS coreg, n.segments AS segments,
                   (m.source_tag IS NOT NULL) AS is_mapped
            FROM raw_num n
            LEFT JOIN (SELECT DISTINCT source_tag FROM mapping_rule) m
              ON m.source_tag = n.tag
        )
        SELECT
            count(*) AS raw_rows,
            sum(CASE WHEN is_mapped THEN 1 ELSE 0 END) AS mapped_tag_rows,
            sum(CASE WHEN is_mapped AND coalesce(segments, '') <> ''
                     THEN 1 ELSE 0 END) AS dropped_segments,
            sum(CASE WHEN is_mapped AND coalesce(segments, '') = ''
                     AND coalesce(coreg, '') <> ''
                     THEN 1 ELSE 0 END) AS dropped_coreg,
            sum(CASE WHEN is_mapped AND coalesce(segments, '') = ''
                     AND coalesce(coreg, '') = ''
                     AND (value IS NULL OR trim(value) = '')
                     THEN 1 ELSE 0 END) AS dropped_null_value
        FROM tagged
        """
    ).fetchone()
    (raw_rows, mapped_tag_rows, dropped_segments, dropped_coreg,
     dropped_null_value) = funnel
    mapped_tag_rows = mapped_tag_rows or 0
    dropped_segments = dropped_segments or 0
    dropped_coreg = dropped_coreg or 0
    dropped_null_value = dropped_null_value or 0
    unmapped_tag_rows = raw_rows - mapped_tag_rows
    eligible_rows = (mapped_tag_rows - dropped_segments - dropped_coreg
                      - dropped_null_value)

    facts = con.execute("SELECT count(*) FROM fact").fetchone()[0]
    arbitrated_away = eligible_rows - facts

    distinct_tags_total = con.execute(
        "SELECT count(DISTINCT tag) FROM raw_num"
    ).fetchone()[0]
    distinct_tags_mapped = con.execute(
        "SELECT count(DISTINCT source_tag) FROM mapping_rule"
    ).fetchone()[0]

    field_coverage = {}
    for field in CANONICAL_FIELDS:
        n_facts, n_companies = con.execute(
            "SELECT count(*), count(DISTINCT cik) FROM fact "
            "WHERE canonical_field = ?",
            [field],
        ).fetchone()
        field_coverage[field] = {"facts": n_facts, "companies": n_companies}

    top_unmapped_tags = [
        {"tag": tag, "rows": rows}
        for tag, rows in con.execute(
            """
            SELECT n.tag, count(*) AS rows
            FROM raw_num n
            LEFT JOIN (SELECT DISTINCT source_tag FROM mapping_rule) m
              ON m.source_tag = n.tag
            WHERE m.source_tag IS NULL
            GROUP BY n.tag
            ORDER BY rows DESC, n.tag
            LIMIT 20
            """
        ).fetchall()
    ]

    return {
        "raw_rows": raw_rows,
        "unmapped_tag_rows": unmapped_tag_rows,
        "mapped_tag_rows": mapped_tag_rows,
        "dropped_segments": dropped_segments,
        "dropped_coreg": dropped_coreg,
        "dropped_null_value": dropped_null_value,
        "eligible_rows": eligible_rows,
        "facts": facts,
        "arbitrated_away": arbitrated_away,
        "distinct_tags_total": distinct_tags_total,
        "distinct_tags_mapped": distinct_tags_mapped,
        "field_coverage": field_coverage,
        "top_unmapped_tags": top_unmapped_tags,
    }
