from datetime import date
from enum import StrEnum
import duckdb
from edgar.curate.mapping import CANONICAL_FIELDS


class FieldStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_DISCLOSED = "NOT_DISCLOSED"
    NOT_YET_FILED = "NOT_YET_FILED"
    UNMAPPED = "UNMAPPED"
    AMBIGUOUS = "AMBIGUOUS"


# Substrings that suggest a tag is about a canonical concept even when no
# mapping rule matches it. Used only to separate "we missed it" (UNMAPPED)
# from "the company never reported it" (NOT_DISCLOSED).
_CONCEPT_HINTS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales"),
    "cost_of_revenue": ("costofrevenue", "costofgoods", "costofsales",
                        "costofproduct", "costofservice"),
    "gross_profit": ("grossprofit", "grossmargin"),
    "operating_income": ("operatingincome", "operatingprofit", "operatingloss"),
    "net_income": ("netincome", "netloss", "profitloss"),
    "total_assets": ("assets",),
    "total_liabilities": ("liabilities",),
    "stockholders_equity": ("equity",),
    "operating_cash_flow": ("operatingactivities",),
    "capex": ("propertyplantandequipment", "capitalexpenditure",
              "productiveassets"),
}


def coverage_map(
    con: duckdb.DuckDBPyConnection,
    cik: int,
    period_end: date,
    as_of: date,
) -> dict[str, FieldStatus]:
    """Classify every canonical field for one company-period per spec §4.6."""
    available = {
        r[0] for r in con.execute(
            "SELECT DISTINCT canonical_field FROM fact "
            "WHERE cik = ? AND period_end = ? AND filed_date <= ?",
            [cik, period_end, as_of],
        ).fetchall()
    }
    # Two mapped tags claiming the same canonical field with different values
    # in the same filing cannot be silently resolved (spec §4.6 AMBIGUOUS).
    ambiguous = {
        r[0] for r in con.execute(
            """
            SELECT canonical_field FROM fact
            WHERE cik = ? AND period_end = ? AND filed_date <= ?
            GROUP BY canonical_field, period_type, filed_date
            HAVING count(DISTINCT value) > 1
            """,
            [cik, period_end, as_of],
        ).fetchall()
    }
    filed_later = {
        r[0] for r in con.execute(
            "SELECT DISTINCT canonical_field FROM fact "
            "WHERE cik = ? AND period_end = ? AND filed_date > ?",
            [cik, period_end, as_of],
        ).fetchall()
    }
    mapped_tags = {
        r[0] for r in con.execute(
            "SELECT DISTINCT source_tag FROM mapping_rule").fetchall()
    }
    company_tags = [
        r[0] for r in con.execute(
            """
            SELECT DISTINCT n.tag FROM raw_num n
            JOIN raw_sub s ON s.adsh = n.adsh
                          AND s.source_quarter = n.source_quarter
            WHERE CAST(s.cik AS BIGINT) = ?
              AND strptime(s.filed, '%Y%m%d')::DATE <= ?
            """,
            [cik, as_of],
        ).fetchall()
    ]
    unmapped_lower = [t.lower() for t in company_tags if t not in mapped_tags]

    out: dict[str, FieldStatus] = {}
    for field in CANONICAL_FIELDS:
        if field in ambiguous:
            out[field] = FieldStatus.AMBIGUOUS
        elif field in available:
            out[field] = FieldStatus.AVAILABLE
        elif field in filed_later:
            out[field] = FieldStatus.NOT_YET_FILED
        elif any(h in t for t in unmapped_lower
                 for h in _CONCEPT_HINTS[field]):
            out[field] = FieldStatus.UNMAPPED
        else:
            out[field] = FieldStatus.NOT_DISCLOSED
    return out
