from pathlib import Path
import duckdb
from edgar.config import get_settings

RAW_DDL = """
CREATE TABLE IF NOT EXISTS raw_sub (
    adsh VARCHAR, cik BIGINT, name VARCHAR, sic VARCHAR, fye VARCHAR,
    form VARCHAR, period VARCHAR, fy VARCHAR, fp VARCHAR, filed VARCHAR,
    prevrpt VARCHAR, detail VARCHAR, nciks VARCHAR, source_quarter VARCHAR
);
CREATE TABLE IF NOT EXISTS raw_num (
    adsh VARCHAR, tag VARCHAR, version VARCHAR, coreg VARCHAR,
    ddate VARCHAR, qtrs VARCHAR, uom VARCHAR, value VARCHAR,
    footnote VARCHAR, segments VARCHAR, source_quarter VARCHAR
);
CREATE TABLE IF NOT EXISTS raw_tag (
    tag VARCHAR, version VARCHAR, custom VARCHAR, abstract VARCHAR,
    datatype VARCHAR, iord VARCHAR, crdr VARCHAR, tlabel VARCHAR,
    doc VARCHAR, source_quarter VARCHAR
);
CREATE TABLE IF NOT EXISTS raw_pre (
    adsh VARCHAR, report VARCHAR, line VARCHAR, stmt VARCHAR,
    inpth VARCHAR, tag VARCHAR, version VARCHAR, plabel VARCHAR,
    negating VARCHAR, source_quarter VARCHAR
);
"""


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    target = path or get_settings().duckdb_path
    target.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(target))


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(RAW_DDL)
