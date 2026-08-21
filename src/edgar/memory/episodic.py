import hashlib
import json
from dataclasses import dataclass
from datetime import date

import duckdb

MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS session (
    session_id VARCHAR PRIMARY KEY,
    cik BIGINT, as_of_date DATE, config_version VARCHAR,
    trace_id VARCHAR, started_at TIMESTAMP DEFAULT current_timestamp,
    question VARCHAR, recalled_conclusion_ids VARCHAR
);
CREATE TABLE IF NOT EXISTS session_conclusion (
    conclusion_id VARCHAR PRIMARY KEY,
    session_id VARCHAR, cik BIGINT, conclusion VARCHAR,
    learned_as_of DATE, trace_id VARCHAR
);
"""


def create_memory_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MEMORY_DDL)


@dataclass(frozen=True)
class Conclusion:
    conclusion_id: str
    session_id: str
    cik: int
    conclusion: str
    learned_as_of: date
    trace_id: str


def save_session(con, *, session_id: str, cik: int, as_of: date,
                 config_version: str, trace_id: str, question: str | None,
                 recalled_conclusion_ids: list[str]) -> None:
    con.execute(
        "INSERT OR REPLACE INTO session "
        "(session_id, cik, as_of_date, config_version, trace_id, question, "
        " recalled_conclusion_ids) VALUES (?,?,?,?,?,?,?)",
        [session_id, cik, as_of, config_version, trace_id, question,
         json.dumps(recalled_conclusion_ids)])


def record_conclusions(con, *, session_id: str, cik: int,
                       conclusions: list[str], learned_as_of: date,
                       trace_id: str) -> list[str]:
    """learned_as_of MUST be the producing session's as_of: a conclusion
    derived from 2025-visible data is a 2025 object even if written today.
    That stamp is the entire leakage guarantee (spec §7.4)."""
    ids = []
    for i, text in enumerate(conclusions):
        cid = "C-" + hashlib.sha256(
            f"{session_id}|{i}|{text}".encode()).hexdigest()[:16]
        con.execute(
            "INSERT OR REPLACE INTO session_conclusion VALUES (?,?,?,?,?,?)",
            [cid, session_id, cik, text, learned_as_of, trace_id])
        ids.append(cid)
    return ids


def recall_conclusions(con, cik: int, as_of: date,
                       limit: int = 5) -> list[Conclusion]:
    rows = con.execute(
        "SELECT conclusion_id, session_id, cik, conclusion, learned_as_of, "
        "trace_id FROM session_conclusion "
        "WHERE cik = ? AND learned_as_of <= ? "
        "ORDER BY learned_as_of DESC, conclusion_id LIMIT ?",
        [cik, as_of, limit]).fetchall()
    return [Conclusion(*r) for r in rows]
