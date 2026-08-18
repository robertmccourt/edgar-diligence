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

    # Wrap all four tables in a single transaction so failure on any table
    # rolls back all deletes, preventing silent inconsistency.
    con.execute("BEGIN TRANSACTION")
    try:
        for kind, table in TABLES.items():
            con.execute(f"DELETE FROM {table} WHERE source_quarter = ?", [q.label])

            # Fetch table columns in ordinal position order to avoid positional
            # alignment risk when the INSERT has an explicit column list.
            table_cols = [r[0] for r in con.execute(
                f"SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = '{table}' AND column_name <> 'source_quarter' "
                f"ORDER BY ordinal_position"
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
            # Explicit column list on INSERT prevents positional alignment risk.
            col_list = ", ".join(f'"{c}"' for c in table_cols)
            con.execute(
                f"""
                INSERT INTO {table} ({col_list}, source_quarter)
                SELECT {select}, '{q.label}'
                FROM read_csv(?, delim='\t', header=true, all_varchar=true,
                              quote='"', escape='"', ignore_errors=true)
                """,
                [str(files[kind])],
            )
            counts[kind] = con.execute(
                f"SELECT count(*) FROM {table} WHERE source_quarter = ?", [q.label]
            ).fetchone()[0]
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return counts
