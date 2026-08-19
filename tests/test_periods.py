from datetime import date
import pytest
from edgar.curate.periods import parse_period, PeriodType, parse_yyyymmdd

def test_qtrs_zero_is_instant():
    p = parse_period("20240630", "0")
    assert p.period_type == PeriodType.INSTANT
    assert p.start is None
    assert p.end == date(2024, 6, 30)

def test_qtrs_one_is_three_month_duration():
    p = parse_period("20240630", "1")
    assert p.period_type == PeriodType.DURATION
    assert p.start == date(2024, 4, 1)
    assert p.end == date(2024, 6, 30)

def test_qtrs_four_is_annual_duration():
    p = parse_period("20240930", "4")
    assert p.start == date(2023, 10, 1)
    assert p.end == date(2024, 9, 30)

def test_qtrs_two_is_six_month_duration():
    p = parse_period("20240630", "2")
    assert p.start == date(2024, 1, 1)

def test_parse_yyyymmdd():
    assert parse_yyyymmdd("20240630") == date(2024, 6, 30)

def test_rejects_bad_date():
    with pytest.raises(ValueError):
        parse_period("2024063", "0")

def test_rejects_negative_qtrs():
    with pytest.raises(ValueError):
        parse_period("20240630", "-1")
