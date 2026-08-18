import pytest
from edgar.ingest.archives import Quarter, enumerate_quarters, archive_url

def test_quarter_label():
    assert Quarter(2024, 1).label == "2024q1"

def test_enumerate_spans_year_boundary():
    qs = enumerate_quarters(Quarter(2023, 3), Quarter(2024, 2))
    assert [q.label for q in qs] == ["2023q3", "2023q4", "2024q1", "2024q2"]

def test_enumerate_single_quarter():
    assert [q.label for q in enumerate_quarters(Quarter(2024, 1), Quarter(2024, 1))] == ["2024q1"]

def test_enumerate_rejects_reversed_range():
    with pytest.raises(ValueError):
        enumerate_quarters(Quarter(2024, 2), Quarter(2024, 1))

def test_archive_url():
    assert archive_url(Quarter(2024, 1)) == (
        "https://www.sec.gov/files/dera/data/"
        "financial-statement-data-sets/2024q1.zip"
    )

def test_quarter_rejects_out_of_range():
    with pytest.raises(ValueError):
        Quarter(2024, 5)
