import duckdb

_SECTOR_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "agriculture"),
    (1000, 1499, "mining"),
    (1500, 1799, "construction"),
    (2000, 3999, "manufacturing"),
    (4000, 4899, "transport_communications"),
    (4900, 4999, "utilities"),
    (5000, 5199, "wholesale"),
    (5200, 5999, "retail"),
    (6000, 6799, "financials"),
    (7000, 8999, "services"),
    (9100, 9999, "public_administration"),
)


def sic_to_sector(sic: str | None) -> str:
    if not sic or not str(sic).strip().isdigit():
        return "unknown"
    code = int(str(sic).strip())
    for lo, hi, name in _SECTOR_RANGES:
        if lo <= code <= hi:
            return name
    return "unknown"


def fye_to_month(fye: str | None) -> int | None:
    if not fye:
        return None
    s = str(fye).strip()
    if len(s) != 4 or not s.isdigit():
        return None
    month = int(s[:2])
    return month if 1 <= month <= 12 else None


COMPANY_DDL = """
CREATE OR REPLACE TABLE company AS
WITH ranked_subs AS (
    SELECT
        CAST(cik AS BIGINT) AS cik,
        name,
        sic,
        fye,
        filed,
        ROW_NUMBER() OVER (
            PARTITION BY CAST(cik AS BIGINT)
            ORDER BY filed DESC, adsh DESC
        ) AS rn,
        MIN(CAST(strptime(filed, '%Y%m%d') AS DATE)) OVER (PARTITION BY CAST(cik AS BIGINT)) AS earliest_filed
    FROM raw_sub
    WHERE cik IS NOT NULL AND trim(CAST(cik AS VARCHAR)) <> ''
),
latest_per_cik AS (
    SELECT
        cik,
        name,
        sic,
        fye,
        earliest_filed AS first_filing_date
    FROM ranked_subs
    WHERE rn = 1
)
SELECT
    cik,
    name,
    sic,
    NULL::VARCHAR AS sector,
    NULL::INTEGER AS fiscal_year_end_month,
    first_filing_date,
    'pending'::VARCHAR AS eligibility_status,
    NULL::VARCHAR AS exclusion_reason
FROM latest_per_cik;
"""


def build_company_table(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(COMPANY_DDL)
    rows = con.execute("SELECT cik, sic FROM company").fetchall()
    # Get the fye values from the latest filing for each company
    fye_query = """
    WITH ranked_subs AS (
        SELECT
            CAST(cik AS BIGINT) AS cik,
            fye,
            ROW_NUMBER() OVER (
                PARTITION BY CAST(cik AS BIGINT)
                ORDER BY filed DESC, adsh DESC
            ) AS rn
        FROM raw_sub
        WHERE cik IS NOT NULL AND trim(CAST(cik AS VARCHAR)) <> ''
    )
    SELECT cik, fye FROM ranked_subs WHERE rn = 1
    """
    fye = {
        int(c): f for c, f in con.execute(fye_query).fetchall()
    }
    con.executemany(
        "UPDATE company SET sector = ?, fiscal_year_end_month = ? WHERE cik = ?",
        [(sic_to_sector(sic), fye_to_month(fye.get(cik)), cik)
         for cik, sic in rows],
    )
    return len(rows)
