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


# Substrings/tags that suggest a company tag is about a canonical concept
# even though no mapping rule matches it. This is a heuristic, not a proof:
# it can under-fire (miss a real synonym, wrongly landing on NOT_DISCLOSED
# for a company that did disclose the concept under an unanticipated tag)
# or over-fire (match an unrelated component tag, wrongly landing on
# UNMAPPED for a company that never reported the aggregate). Its residual
# error rate is not assumed to be zero here; it is measured empirically by
# the mapping-recall sampling step, which is the authority on how well this
# heuristic actually performs.
#
# _CONCEPT_HINTS entries are substrings matched anywhere in the (lowercased)
# tag name. _CONCEPT_EXACT_HINTS entries must match the whole tag name —
# used for aggregate concepts (e.g. "assets") whose bare word would
# otherwise match every component line item that happens to contain it
# (IntangibleAssetsNetExcludingGoodwill, DeferredTaxAssetsNet, ...).
_CONCEPT_HINTS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "netsales", "salesrevenue", "totalsales",
               "merchandisesales", "productsales"),
    "cost_of_revenue": ("costofrevenue", "costofgoods", "costofsales",
                        "costofproduct", "costofservice"),
    "gross_profit": ("grossprofit", "grossmargin"),
    "operating_income": ("operatingincome", "operatingprofit", "operatingloss"),
    "net_income": ("netincome", "netloss", "profitloss"),
    "total_assets": ("totalassets",),
    "total_liabilities": ("totalliabilities",),
    "stockholders_equity": ("stockholdersequity", "shareholdersequity",
                            "totalequity", "membersequity", "partnerscapital",
                            "equityattributabletoparent"),
    "operating_cash_flow": ("operatingactivities",),
    "capex": ("propertyplantandequipment", "capitalexpenditure",
              "productiveassets"),
}

_CONCEPT_EXACT_HINTS: dict[str, frozenset[str]] = {
    "total_assets": frozenset({"assets"}),
    "total_liabilities": frozenset({"liabilities"}),
}


def _looks_related(field: str, tags_lower: list[str]) -> bool:
    substrings = _CONCEPT_HINTS[field]
    exacts = _CONCEPT_EXACT_HINTS.get(field, frozenset())
    return (any(h in t for t in tags_lower for h in substrings)
            or any(t in exacts for t in tags_lower))


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
              AND strptime(n.ddate, '%Y%m%d')::DATE = ?
            """,
            [cik, as_of, period_end],
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
        elif _looks_related(field, unmapped_lower):
            out[field] = FieldStatus.UNMAPPED
        else:
            out[field] = FieldStatus.NOT_DISCLOSED
    return out
