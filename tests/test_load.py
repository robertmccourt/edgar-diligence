from edgar.db import connect, init_schema
from edgar.ingest.archives import Quarter
from edgar.ingest.load import load_quarter

def _files(tmp_path):
    (tmp_path / "sub.txt").write_text(
        "adsh\tcik\tname\tsic\tfye\tform\tperiod\tfy\tfp\tfiled\tprevrpt\tdetail\tnciks\n"
        "0000320193-24-000081\t320193\tAPPLE INC\t3571\t0930\t10-Q\t20240630\t2024\tQ3\t20240802\t0\t1\t1\n")
    (tmp_path / "num.txt").write_text(
        "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n"
        "0000320193-24-000081\tRevenues\tus-gaap/2024\t\t20240630\t1\tUSD\t85777000000\t\n")
    (tmp_path / "tag.txt").write_text(
        "tag\tversion\tcustom\tabstract\tdatatype\tiord\tcrdr\ttlabel\tdoc\n"
        "Revenues\tus-gaap/2024\t0\t0\tmonetary\tD\tC\tRevenues\tTotal revenue.\n")
    (tmp_path / "pre.txt").write_text(
        "adsh\treport\tline\tstmt\tinpth\ttag\tversion\tplabel\tnegating\n"
        "0000320193-24-000081\t2\t1\tIS\t0\tRevenues\tus-gaap/2024\tNet sales\t0\n")
    return {k: tmp_path / f"{k}.txt" for k in ("sub", "num", "tag", "pre")}

def test_load_populates_all_tables(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    counts = load_quarter(con, _files(tmp_path), Quarter(2024, 3))
    assert counts == {"sub": 1, "num": 1, "tag": 1, "pre": 1}
    assert con.execute("SELECT source_quarter FROM raw_num").fetchone()[0] == "2024q3"

def test_load_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)
    load_quarter(con, f, Quarter(2024, 3))
    load_quarter(con, f, Quarter(2024, 3))
    assert con.execute("SELECT count(*) FROM raw_num").fetchone()[0] == 1

def test_load_handles_quoted_field_with_embedded_newline(tmp_path):
    """DERA `segments` values carry embedded newlines and doubled quotes.

    Verified against 2024q1: ~55% of num.txt rows have a non-empty segments
    value and some contain raw newlines inside a quoted field. A parser with
    quoting disabled splits those rows in half and silently corrupts them.
    """
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)
    f["num"].write_text(
        "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\tfootnote\n"
        '0000320193-24-000081\tRevenues\tus-gaap/2024\t20240630\t1\tUSD\t'
        '"InvestmentIdentifier=Abaco Energy, LLC\nPreferred ""Equity"";"'
        "\t\t85777000000\t\n"
    )
    load_quarter(con, f, Quarter(2024, 3))
    rows = con.execute("SELECT value, segments FROM raw_num").fetchall()
    assert len(rows) == 1, f"quoted field split the row: {rows}"
    assert rows[0][0] == "85777000000"
    assert "Abaco Energy, LLC" in rows[0][1]
    assert "\n" in rows[0][1]

def test_load_handles_missing_optional_column(tmp_path):
    """num.txt has no 'segments' column in older quarters; load must NULL-fill."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    load_quarter(con, _files(tmp_path), Quarter(2024, 3))
    assert con.execute("SELECT segments FROM raw_num").fetchone()[0] is None

def test_load_keeps_quarters_separate(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)
    load_quarter(con, f, Quarter(2024, 3))
    load_quarter(con, f, Quarter(2024, 4))
    assert con.execute("SELECT count(*) FROM raw_num").fetchone()[0] == 2
