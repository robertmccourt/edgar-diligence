from datetime import date

from edgar.db import connect
from edgar.memory.episodic import (
    create_memory_tables, recall_conclusions, record_conclusions,
    save_session)


def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_memory_tables(con)
    return con


def _session(con, sid, as_of, conclusions):
    save_session(con, session_id=sid, cik=1, as_of=as_of,
                 config_version="v1", trace_id=f"tr-{sid}", question=None,
                 recalled_conclusion_ids=[])
    record_conclusions(con, session_id=sid, cik=1, conclusions=conclusions,
                       learned_as_of=as_of, trace_id=f"tr-{sid}")


def test_recall_blocks_later_learned_conclusions(tmp_path):
    con = _db(tmp_path)
    _session(con, "s23", date(2023, 6, 1), ["margins compressed in 2022"])
    _session(con, "s25", date(2025, 6, 1), ["margins recovered by 2025"])
    got = recall_conclusions(con, cik=1, as_of=date(2023, 12, 31))
    assert [c.conclusion for c in got] == ["margins compressed in 2022"]
    got_25 = recall_conclusions(con, cik=1, as_of=date(2025, 12, 31))
    assert len(got_25) == 2
    assert got_25[0].learned_as_of == date(2025, 6, 1)   # recency first


def test_recall_is_cik_scoped_and_limited(tmp_path):
    con = _db(tmp_path)
    for i in range(7):
        _session(con, f"s{i}", date(2023, 1, 1 + i), [f"conclusion {i}"])
    save_session(con, session_id="other", cik=2, as_of=date(2023, 6, 1),
                 config_version="v1", trace_id="t", question=None,
                 recalled_conclusion_ids=[])
    record_conclusions(con, session_id="other", cik=2,
                       conclusions=["theirs"],
                       learned_as_of=date(2023, 6, 1), trace_id="t")
    got = recall_conclusions(con, cik=1, as_of=date(2024, 1, 1), limit=5)
    assert len(got) == 5 and all(c.cik == 1 for c in got)


def test_conclusion_ids_are_stable(tmp_path):
    con = _db(tmp_path)
    ids1 = record_conclusions(con, session_id="s", cik=1, conclusions=["x"],
                              learned_as_of=date(2023, 1, 1), trace_id="t")
    ids2 = record_conclusions(con, session_id="s", cik=1, conclusions=["x"],
                              learned_as_of=date(2023, 1, 1), trace_id="t")
    assert ids1 == ids2
    n = con.execute("SELECT count(*) FROM session_conclusion").fetchone()[0]
    assert n == 1
