from pathlib import Path
import duckdb
from edgar.ingest.archives import Quarter

TABLES = {"sub": "raw_sub", "num": "raw_num", "tag": "raw_tag", "pre": "raw_pre"}


def load_quarter(
    con: duckdb.DuckDBPyConnection,
    files: dict[str, Path],
    q: Quarter,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind, table in TABLES.items():
        con.execute(f"DELETE FROM {table} WHERE source_quarter = ?", [q.label])

        table_cols = [r[0] for r in con.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND column_name <> 'source_quarter'"
        ).fetchall()]

        # DERA adds and removes optional columns between quarters (e.g. the
        # 'segments' column in num.txt, added in the Dec-2024 reprocessing).
        # Intersect the file's actual header
        # with the table's columns and NULL-fill the rest, so neither an
        # extra nor a missing optional column breaks the load.
        with files[kind].open(encoding="utf-8", errors="replace") as fh:
            file_cols = set(fh.readline().rstrip("\n\r").split("\t"))

        select = ", ".join(
            f'"{c}"' if c in file_cols else f'NULL AS "{c}"'
            for c in table_cols
        )
        con.execute(
            f"""
            INSERT INTO {table}
            SELECT {select}, '{q.label}'
            FROM read_csv(?, delim='\t', header=true, all_varchar=true,
                          quote='"', escape='"', ignore_errors=true)
            """,
            [str(files[kind])],
        )
        counts[kind] = con.execute(
            f"SELECT count(*) FROM {table} WHERE source_quarter = ?", [q.label]
        ).fetchone()[0]
    return counts
