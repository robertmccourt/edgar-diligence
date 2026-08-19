# tests/test_asof.py
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.query.asof import get_facts_asof, restatement_history

def _restated(tmp_path):
    """Revenue for Q1 2023 first reported as 100, later restated to 94."""
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("f1", 1, "revenue", 100.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2023, 5, 10), "acc-1", "Revenues", "MR-0003", 1.0, "2023q2"),
            ("f2", 1, "revenue", 94.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2023, 11, 8), "acc-2", "Revenues", "MR-0003", 1.0, "2023q4"),
        ],
    )
    return con

def test_before_first_filing_returns_nothing(tmp_path):
    con = _restated(tmp_path)
    assert get_facts_asof(con, 1, ["revenue"],
                          date(2023, 1, 1), date(2023, 12, 31),
                          date(2023, 4, 30)) == []

def test_between_filings_returns_original_value(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2023, 9, 1))
    assert len(got) == 1 and got[0].value == 100.0
    assert got[0].accession == "acc-1"

def test_after_restatement_returns_corrected_value(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 1, 1))
    assert len(got) == 1 and got[0].value == 94.0
    assert got[0].accession == "acc-2"

def test_as_of_equals_filed_date_is_inclusive(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2023, 5, 10))
    assert got[0].value == 100.0

def test_restatement_history_returns_both_versions(tmp_path):
    """Both filed versions of one genuine restatement, oldest first.

    The figure is now addressed by its full identity — period_end alone
    does not identify one — but the property under test is unchanged.
    """
    con = _restated(tmp_path)
    hist = restatement_history(con, 1, "revenue", date(2023, 3, 31),
                               period_start=date(2023, 1, 1),
                               period_type="duration", unit="USD")
    assert [h.value for h in hist] == [100.0, 94.0]
    assert [h.accession for h in hist] == ["acc-1", "acc-2"]

def test_unknown_field_returns_empty(tmp_path):
    con = _restated(tmp_path)
    assert get_facts_asof(con, 1, ["capex"], date(2023, 1, 1),
                          date(2023, 12, 31), date(2024, 1, 1)) == []

def test_period_type_partition_keeps_duration_and_instant_separate(tmp_path):
    """duration and instant facts for the same field/period must never be
    ranked against each other. If `period_type` were dropped from the
    window's PARTITION BY, the instant fact below would be silently
    shadowed by whichever duration row has the latest filed_date."""
    con = _restated(tmp_path)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("f3", 1, "revenue", 50.0, "USD", "instant",
             None, date(2023, 3, 31), "2023", "Q1",
             date(2023, 6, 1), "acc-3", "Revenues", "MR-0003", 1.0, "2023q2"),
        ],
    )
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 1, 1))
    assert len(got) == 2
    assert {g.period_type for g in got} == {"duration", "instant"}

def test_multiple_fields_returns_rows_for_each(tmp_path):
    con = _restated(tmp_path)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("f4", 1, "opex", 30.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2023, 5, 10), "acc-4", "OperatingExpenses", "MR-0004", 1.0, "2023q2"),
        ],
    )
    got = get_facts_asof(con, 1, ["revenue", "opex"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2023, 9, 1))
    assert {g.canonical_field for g in got} == {"revenue", "opex"}
    assert len(got) == 2


def test_overlapping_period_lengths_are_all_returned(tmp_path):
    """A 10-Q reports a 3-month and a year-to-date figure that END on the
    same day; a 10-K adds the annual figure. These are three distinct
    economic quantities, not three versions of one. If `period_start` were
    dropped from the window's PARTITION BY they would compete for rank 1
    and two of the three would be silently discarded.

    Modelled on real data: cik=1536089, net_income, all ending 2023-03-31,
    carrying 12-month, 6-month and 3-month figures at once.
    """
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # 12-month, filed with the 10-K
            ("g1", 9, "net_income", -1728362.0, "USD", "duration",
             date(2022, 4, 1), date(2023, 3, 31), "2023", "FY",
             date(2024, 4, 16), "acc-k", "NetIncomeLoss", "MR-0005", 1.0, "q"),
            # 6-month, same filing
            ("g2", 9, "net_income", -1728362.0, "USD", "duration",
             date(2022, 10, 1), date(2023, 3, 31), "2023", "Q2",
             date(2024, 4, 16), "acc-k", "NetIncomeLoss", "MR-0005", 1.0, "q"),
            # 3-month, filed later in a 10-Q
            ("g3", 9, "net_income", -840746.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2024, 6, 7), "acc-q", "NetIncomeLoss", "MR-0005", 1.0, "q"),
        ],
    )
    got = get_facts_asof(con, 9, ["net_income"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 12, 31))
    assert len(got) == 3
    assert {g.period_start for g in got} == {
        date(2022, 4, 1), date(2022, 10, 1), date(2023, 1, 1)}
    assert {g.value for g in got} == {-1728362.0, -840746.0}


def test_restatement_within_one_period_length_still_collapses(tmp_path):
    """Adding period_start must not defeat the point-in-time rule: two
    filings of the *same* start/end pair are still versions of one figure,
    and only the latest visible one is returned."""
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("h1", 9, "net_income", -840746.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2024, 4, 16), "acc-a", "NetIncomeLoss", "MR-0005", 1.0, "q"),
            ("h2", 9, "net_income", -900000.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2024, 6, 7), "acc-b", "NetIncomeLoss", "MR-0005", 1.0, "q"),
            ("h3", 9, "net_income", -1728362.0, "USD", "duration",
             date(2022, 10, 1), date(2023, 3, 31), "2023", "Q2",
             date(2024, 6, 7), "acc-b", "NetIncomeLoss", "MR-0005", 1.0, "q"),
        ],
    )
    got = get_facts_asof(con, 9, ["net_income"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 12, 31))
    assert len(got) == 2
    by_start = {g.period_start: g for g in got}
    assert by_start[date(2023, 1, 1)].value == -900000.0
    assert by_start[date(2023, 1, 1)].accession == "acc-b"
    assert by_start[date(2022, 10, 1)].value == -1728362.0


def test_same_figure_in_two_currencies_returns_both(tmp_path):
    """A filer reporting the same balance-sheet line in USD and CNY has
    filed two figures, not two versions of one. Without `unit` in the
    window partition they compete for rank 1 and the caller gets whichever
    the tiebreak picks — silently, under a valid fact_id and a real
    accession, so a consumer comparing across companies reads CNY as USD.

    Modelled on cik=1296774, total_assets 2023-12-31, which carries
    USD 205,617,546 and CNY 1,310,318,375 in the same accession.
    """
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("u1", 1296774, "total_assets", 205617546.0, "USD", "instant",
             None, date(2023, 12, 31), "2023", "FY",
             date(2024, 3, 15), "acc-1", "Assets", "MR-0012", 1.0, "2024q1"),
            ("u2", 1296774, "total_assets", 1310318375.0, "CNY", "instant",
             None, date(2023, 12, 31), "2023", "FY",
             date(2024, 3, 15), "acc-1", "Assets", "MR-0012", 1.0, "2024q1"),
        ],
    )
    got = get_facts_asof(con, 1296774, ["total_assets"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 12, 31))
    assert len(got) == 2
    by_unit = {g.unit: g for g in got}
    assert set(by_unit) == {"USD", "CNY"}
    assert by_unit["USD"].value == 205617546.0
    assert by_unit["CNY"].value == 1310318375.0
    assert by_unit["USD"].fact_id != by_unit["CNY"].fact_id


def test_restatement_within_one_unit_still_collapses(tmp_path):
    """Adding unit must not defeat the point-in-time rule: two filings of
    the same figure in the same currency are still versions of one figure,
    and the other currency is unaffected."""
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("u1", 7, "total_assets", 100.0, "USD", "instant",
             None, date(2023, 12, 31), "2023", "FY",
             date(2024, 3, 15), "acc-1", "Assets", "MR-0012", 1.0, "2024q1"),
            ("u2", 7, "total_assets", 94.0, "USD", "instant",
             None, date(2023, 12, 31), "2023", "FY",
             date(2024, 8, 1), "acc-2", "Assets", "MR-0012", 1.0, "2024q3"),
            ("u3", 7, "total_assets", 700.0, "CNY", "instant",
             None, date(2023, 12, 31), "2023", "FY",
             date(2024, 3, 15), "acc-1", "Assets", "MR-0012", 1.0, "2024q1"),
        ],
    )
    got = get_facts_asof(con, 7, ["total_assets"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 12, 31))
    assert len(got) == 2
    by_unit = {g.unit: g for g in got}
    assert by_unit["USD"].value == 94.0
    assert by_unit["USD"].accession == "acc-2"
    assert by_unit["CNY"].value == 700.0


def _mixed_lengths(tmp_path):
    """One period_end carrying a 12-month and a 3-month figure.

    Modelled on cik=1855631, operating_cash_flow, period_end 2022-12-31,
    where a 12-month -16,865,274 and a -200 were returned as if they were
    two versions of one number.
    """
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # 12-month figure, and its later restatement
            ("m1", 1855631, "operating_cash_flow", -16865274.0, "USD",
             "duration", date(2022, 1, 1), date(2022, 12, 31), "2022", "FY",
             date(2023, 3, 31), "acc-1",
             "NetCashProvidedByUsedInOperatingActivities", "MR-0016", 1.0, "q"),
            ("m2", 1855631, "operating_cash_flow", -16900000.0, "USD",
             "duration", date(2022, 1, 1), date(2022, 12, 31), "2022", "FY",
             date(2024, 3, 31), "acc-2",
             "NetCashProvidedByUsedInOperatingActivities", "MR-0016", 1.0, "q"),
            # 3-month figure ending the same day — a different quantity
            ("m3", 1855631, "operating_cash_flow", -200.0, "USD",
             "duration", date(2022, 10, 1), date(2022, 12, 31), "2022", "Q4",
             date(2023, 3, 31), "acc-1",
             "NetCashProvidedByUsedInOperatingActivities", "MR-0016", 1.0, "q"),
        ],
    )
    return con


def test_restatement_history_returns_only_the_requested_period_length(tmp_path):
    """period_end alone does not identify a figure: 14,162 (cik, field,
    period_end) triples in the smoke build span more than one
    (period_start, period_type). Asking for the 12-month figure must not
    splice the co-filed 3-month figure into its revision chain — this
    function is the only place supersession is derived, so whatever it
    returns is what a consumer believes superseded what."""
    con = _mixed_lengths(tmp_path)
    hist = restatement_history(
        con, 1855631, "operating_cash_flow", date(2022, 12, 31),
        period_start=date(2022, 1, 1), period_type="duration", unit="USD")
    assert [h.value for h in hist] == [-16865274.0, -16900000.0]
    assert all(h.period_start == date(2022, 1, 1) for h in hist)
    assert -200.0 not in [h.value for h in hist]


def test_restatement_history_returns_the_short_period_when_asked(tmp_path):
    """The 3-month figure is still reachable — it is a figure in its own
    right, not noise to be filtered away."""
    con = _mixed_lengths(tmp_path)
    hist = restatement_history(
        con, 1855631, "operating_cash_flow", date(2022, 12, 31),
        period_start=date(2022, 10, 1), period_type="duration", unit="USD")
    assert [h.value for h in hist] == [-200.0]


def test_restatement_history_separates_units(tmp_path):
    """Two currencies for one figure are two chains, not one."""
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("n1", 5, "total_assets", 205617546.0, "USD", "instant",
             None, date(2023, 12, 31), "2023", "FY",
             date(2024, 3, 15), "acc-1", "Assets", "MR-0012", 1.0, "q"),
            ("n2", 5, "total_assets", 1310318375.0, "CNY", "instant",
             None, date(2023, 12, 31), "2023", "FY",
             date(2024, 3, 15), "acc-1", "Assets", "MR-0012", 1.0, "q"),
        ],
    )
    usd = restatement_history(con, 5, "total_assets", date(2023, 12, 31),
                              period_start=None, period_type="instant",
                              unit="USD")
    assert [h.value for h in usd] == [205617546.0]
    cny = restatement_history(con, 5, "total_assets", date(2023, 12, 31),
                              period_start=None, period_type="instant",
                              unit="CNY")
    assert [h.value for h in cny] == [1310318375.0]
