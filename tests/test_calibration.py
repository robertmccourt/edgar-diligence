from edgar.eval.calibration import cohens_kappa

_HEADER = "claim_text,claim_type,judge_status,judge_reason,human_status\n"


def _csv(tmp_path, rows):
    p = tmp_path / "labels.csv"
    p.write_text(_HEADER + "".join(rows))
    return p


def test_perfect_agreement_is_kappa_one(tmp_path):
    rows = [f"c{i},NUMERIC,SUPPORTED,r,SUPPORTED\n" for i in range(3)]
    rows += [f"d{i},NUMERIC,UNSUPPORTED,r,UNSUPPORTED\n" for i in range(3)]
    kappa, n = cohens_kappa(_csv(tmp_path, rows))
    assert n == 6 and kappa == 1.0


def test_unlabeled_rows_are_skipped(tmp_path):
    rows = ["a,NUMERIC,SUPPORTED,r,SUPPORTED\n",
            "b,NUMERIC,SUPPORTED,r,\n"]
    kappa, n = cohens_kappa(_csv(tmp_path, rows))
    assert n == 1


def test_systematic_disagreement_is_nonpositive(tmp_path):
    rows = [f"c{i},NUMERIC,SUPPORTED,r,UNSUPPORTED\n" for i in range(4)]
    rows += [f"d{i},NUMERIC,UNSUPPORTED,r,SUPPORTED\n" for i in range(4)]
    kappa, _ = cohens_kappa(_csv(tmp_path, rows))
    assert kappa <= 0
