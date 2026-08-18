import pytest
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

def test_load_places_values_in_correct_columns(tmp_path):
    """Prove explicit column list and ORDER BY ordinal_position land values correctly."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    load_quarter(con, _files(tmp_path), Quarter(2024, 3))

    # Verify a specific field value reads from the correct column, not shifted.
    # Note: cik is BIGINT in schema, so it's auto-cast from CSV string to integer.
    row = con.execute("SELECT cik, name, form FROM raw_sub").fetchone()
    assert row[0] == 320193, f"cik should be 320193, got {row[0]}"
    assert row[1] == "APPLE INC", f"name should be APPLE INC, got {row[1]}"
    assert row[2] == "10-Q", f"form should be 10-Q, got {row[2]}"

def test_load_transaction_consistency_on_mid_loop_failure(tmp_path):
    """Prove mid-loop failure leaves no partial state; transaction rolls back entire quarter."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)

    # First load succeeds for all tables for 2024q3
    f = _files(tmp_path)
    load_quarter(con, f, Quarter(2024, 3))

    # Verify baseline: all tables have 2024q3 data
    assert con.execute("SELECT count(*) FROM raw_sub WHERE source_quarter='2024q3'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM raw_num WHERE source_quarter='2024q3'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM raw_tag WHERE source_quarter='2024q3'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM raw_pre WHERE source_quarter='2024q3'").fetchone()[0] == 1

    # Now attempt to load 2024q3 again with missing pre.txt to trigger mid-loop failure
    bad_files = _files(tmp_path)
    bad_files["pre"].unlink()  # Delete the pre.txt file to cause an error when opening

    try:
        load_quarter(con, bad_files, Quarter(2024, 3))
        assert False, "Expected exception from missing pre.txt"
    except FileNotFoundError:
        pass  # Expected: pre.txt is missing

    # Verify transaction rollback: 2024q3 still has original data because entire
    # transaction was rolled back when pre.txt open failed. No partial state.
    assert con.execute("SELECT count(*) FROM raw_sub WHERE source_quarter='2024q3'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM raw_num WHERE source_quarter='2024q3'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM raw_tag WHERE source_quarter='2024q3'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM raw_pre WHERE source_quarter='2024q3'").fetchone()[0] == 1

def test_load_reject_count_clean_file_no_warning(tmp_path, capsys):
    """Clean file produces zero rejects and no warning."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)
    load_quarter(con, f, Quarter(2024, 3))

    # Verify no warning was printed
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out, f"Unexpected warning: {captured.out}"

    # Verify counts match expected (no rejects)
    assert con.execute("SELECT count(*) FROM raw_num WHERE source_quarter='2024q3'").fetchone()[0] == 1

def test_load_reject_count_under_threshold_with_warning(tmp_path, capsys):
    """File with malformed row under 1% threshold loads with warning."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)

    # Add one unparseable row to num.txt: 1 bad out of 101 = 0.99% reject rate (under 1% threshold).
    # Row with wrong column count (2 fields instead of 9) will be skipped by ignore_errors=true.
    good_rows = "\n".join([
        "0000320193-24-000081\tRevenues\tus-gaap/2024\t\t20240630\t1\tUSD\t85777000000\t"
        for _ in range(100)
    ])
    f["num"].write_text(
        "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n" +
        good_rows + "\n" +
        "0000320193-24-000081\tRevenues"  # Malformed: only 2 fields instead of 9; ignore_errors=true skips it
    )

    load_quarter(con, f, Quarter(2024, 3))

    # Verify warning was printed with correct counts and reject rate
    captured = capsys.readouterr()
    assert "WARNING" in captured.out, f"Expected WARNING in output, got: {captured.out}"
    # Check that warning includes expected counts: expected 101, inserted 100, rejected 1
    assert "101" in captured.out, f"Expected '101' (expected rows) in warning, got: {captured.out}"
    assert "100" in captured.out, f"Expected '100' (inserted rows) in warning, got: {captured.out}"
    assert "1" in captured.out, f"Expected '1' (rejected rows) in warning, got: {captured.out}"

    # Verify exactly one row was rejected (not just >= 100)
    assert con.execute("SELECT count(*) FROM raw_num WHERE source_quarter='2024q3'").fetchone()[0] == 100

def test_load_reject_count_over_threshold_raises_and_rolls_back(tmp_path):
    """File with rejects over 1% threshold raises and rolls back."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)

    # First load succeeds
    f = _files(tmp_path)
    load_quarter(con, f, Quarter(2024, 3))
    assert con.execute("SELECT count(*) FROM raw_num WHERE source_quarter='2024q3'").fetchone()[0] == 1

    # Now load with 50+ malformed rows so reject rate > 1%
    # Create a num.txt with 100 good rows and 2 malformed rows = 2% reject rate
    good_rows = "\n".join([
        "0000320193-24-000081\tRevenues\tus-gaap/2024\t\t20240630\t1\tUSD\t85777000000\t"
        for _ in range(100)
    ])
    bad_file = _files(tmp_path)
    bad_file["num"].write_text(
        "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n" +
        good_rows + "\n" +
        "malformed" + "\n" +
        "also_bad"
    )

    # Attempt to load should raise ValueError due to high reject rate
    with pytest.raises(ValueError) as exc_info:
        load_quarter(con, bad_file, Quarter(2024, 3))

    # Verify error message mentions reject rate and threshold
    error_msg = str(exc_info.value)
    assert "rejected" in error_msg
    assert "threshold" in error_msg

    # Verify transaction rolled back: 2024q3 still has original data
    assert con.execute("SELECT count(*) FROM raw_num WHERE source_quarter='2024q3'").fetchone()[0] == 1

def test_load_embedded_newline_counted_correctly_by_csv_reader(tmp_path):
    """csv.reader counts embedded-newline row as 1, not 2; no false reject signal."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)

    # Create num.txt with embedded newline in quoted field (like real DERA segments column)
    f["num"].write_text(
        "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\tfootnote\n"
        "0000320193-24-000081\tRevenues\tus-gaap/2024\t20240630\t1\tUSD\t"
        '"Company\nName"\t\t85777000000\t\n'
    )

    # This should load without error or warning (csv.reader sees 1 data row, DuckDB inserts 1)
    load_quarter(con, f, Quarter(2024, 3))

    # Verify row was inserted correctly
    row = con.execute("SELECT value, segments FROM raw_num").fetchone()
    assert row[0] == "85777000000"
    assert "Company\nName" == row[1]
