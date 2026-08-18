import zipfile, pytest
from edgar.ingest.extract import extract_archive, validate_columns, SchemaMismatch

SUB_HEADER = "adsh\tcik\tname\tsic\tfye\tform\tperiod\tfy\tfp\tfiled\tprevrpt\tdetail\tnciks"

def _zip(tmp_path, files):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return z

def test_extract_returns_four_paths(tmp_path):
    z = _zip(tmp_path, {
        "sub.txt": SUB_HEADER + "\n",
        "num.txt": "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n",
        "tag.txt": "tag\tversion\tcustom\tabstract\tdatatype\tiord\tcrdr\ttlabel\tdoc\n",
        "pre.txt": "adsh\treport\tline\tstmt\tinpth\ttag\tversion\tplabel\tnegating\n",
    })
    out = extract_archive(z, tmp_path / "x")
    assert set(out) == {"sub", "num", "tag", "pre"}
    assert out["sub"].exists()

def test_validate_columns_accepts_superset(tmp_path):
    p = tmp_path / "num.txt"
    p.write_text("adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\tsegments\n")
    validate_columns(p, "num")  # extra 'segments' column is fine

def test_validate_columns_rejects_missing(tmp_path):
    p = tmp_path / "num.txt"
    p.write_text("adsh\ttag\tvalue\n")
    with pytest.raises(SchemaMismatch) as e:
        validate_columns(p, "num")
    assert "ddate" in str(e.value)

def test_extract_rejects_incomplete_archive(tmp_path):
    z = _zip(tmp_path, {"sub.txt": SUB_HEADER + "\n"})
    with pytest.raises(SchemaMismatch):
        extract_archive(z, tmp_path / "y")
