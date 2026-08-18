import duckdb

EXCLUDED_SECTORS: frozenset[str] = frozenset({"financials", "utilities"})
MIN_QUARTERS: int = 12
MIN_FIELDS: int = 5


def apply_eligibility(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Classify every company per spec §4.7 and report removal counts.

    Rules are applied in order; the first failure wins, so exclusion_reason
    is always the most fundamental disqualifier.
    """
    con.execute(
        "UPDATE company SET eligibility_status='pending', exclusion_reason=NULL")

    con.execute(
        f"""
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='excluded_sector'
        WHERE sector IN ({", ".join("?" * len(EXCLUDED_SECTORS))})
        """,
        sorted(EXCLUDED_SECTORS),
    )

    con.execute(
        """
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='insufficient_history'
        WHERE eligibility_status='pending' AND cik IN (
            SELECT cik FROM fact GROUP BY cik
            HAVING count(DISTINCT period_end) < ?
        )
        """,
        [MIN_QUARTERS],
    )

    con.execute(
        """
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='no_facts'
        WHERE eligibility_status='pending'
          AND cik NOT IN (SELECT DISTINCT cik FROM fact)
        """
    )

    con.execute(
        """
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='insufficient_field_coverage'
        WHERE eligibility_status='pending' AND cik IN (
            SELECT cik FROM fact GROUP BY cik
            HAVING count(DISTINCT canonical_field) < ?
        )
        """,
        [MIN_FIELDS],
    )

    con.execute(
        "UPDATE company SET eligibility_status='eligible' "
        "WHERE eligibility_status='pending'")

    counts = {
        r[0]: r[1] for r in con.execute(
            "SELECT coalesce(exclusion_reason, eligibility_status), count(*) "
            "FROM company GROUP BY 1"
        ).fetchall()
    }
    return counts
