import zipfile
from pathlib import Path

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "sub": {"adsh", "cik", "name", "sic", "fye", "form",
            "period", "fy", "fp", "filed"},
    "num": {"adsh", "tag", "version", "ddate", "qtrs", "uom", "value"},
    "tag": {"tag", "version", "custom", "datatype", "iord", "crdr"},
    "pre": {"adsh", "stmt", "tag", "version", "plabel"},
}


class SchemaMismatch(Exception):
    """A DERA file does not have the columns this codebase assumes."""


def validate_columns(path: Path, kind: str) -> None:
    with path.open(encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n\r").split("\t")
    missing = REQUIRED_COLUMNS[kind] - set(header)
    if missing:
        raise SchemaMismatch(
            f"{path.name} missing required columns {sorted(missing)}; "
            f"found {header}. Re-run Task 1 verification."
        )


def extract_archive(zip_path: Path, dest_dir: Path) -> dict[str, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for kind in REQUIRED_COLUMNS:
            member = f"{kind}.txt"
            if member not in names:
                raise SchemaMismatch(
                    f"{zip_path.name} has no {member}; found {sorted(names)}"
                )
            zf.extract(member, dest_dir)
            path = dest_dir / member
            validate_columns(path, kind)
            out[kind] = path
    return out
