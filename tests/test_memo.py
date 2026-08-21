from datetime import date

from edgar.agent.memo import Claim, Memo, MemoSection, render_markdown


def _memo():
    return Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      narrative="Revenue rose.",
                                      claims=[Claim(text="Revenue was 100",
                                                    citations=["fA"])]),
                          MemoSection(slug="working_capital",
                                      title="Working capital",
                                      status="status_code",
                                      status_note="inventory: "
                                                  "NOT_DISCLOSED")],
                hypotheses=[Claim(text="Pricing lags peers",
                                  is_hypothesis=True)])


def test_render_includes_citations_and_status_codes():
    md = render_markdown(_memo())
    assert "## 2. Growth" not in md
    assert "## Growth" in md
    assert "[fA]" in md
    assert "NOT_DISCLOSED" in md
    assert "Hypothesis" in md and "Pricing lags peers" in md


def test_render_shows_as_of_and_identity():
    md = render_markdown(_memo())
    assert "2023-06-01" in md and "ACME" in md
