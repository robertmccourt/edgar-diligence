import ast
import hashlib
import json
from datetime import date

import duckdb

from edgar.tools.schemas import Computation

DERIVATION_DDL = """
CREATE TABLE IF NOT EXISTS derivation (
    derivation_id VARCHAR PRIMARY KEY,
    expression VARCHAR,
    inputs_json VARCHAR,
    value DOUBLE,
    as_of DATE
);
"""


class ComputeError(ValueError):
    """Raised for any rejected computation. Message is agent-facing."""


def create_derivation_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DERIVATION_DDL)


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _eval(node, values, types):
    """Evaluate, returning (value, period_type|None). Constants carry None;
    +/- demand one period_type among non-constant operands."""
    if isinstance(node, ast.Expression):
        return _eval(node.body, values, types)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or \
                isinstance(node.value, bool):
            raise ComputeError(f"constant {node.value!r} not allowed")
        return float(node.value), None
    if isinstance(node, ast.Name):
        return values[node.id], types[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v, t = _eval(node.operand, values, types)
        return -v, t
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        lv, lt = _eval(node.left, values, types)
        rv, rt = _eval(node.right, values, types)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if lt is not None and rt is not None and lt != rt:
                raise ComputeError(
                    f"additive mixing of period_type {lt!r} and {rt!r}; "
                    "add/subtract requires like types (ratios are allowed)")
            t = lt or rt
        else:
            t = None  # a ratio/product is dimensionally its own thing
        if isinstance(node.op, ast.Add):
            return lv + rv, t
        if isinstance(node.op, ast.Sub):
            return lv - rv, t
        if isinstance(node.op, ast.Mult):
            return lv * rv, t
        if rv == 0:
            raise ComputeError("division by zero")
        return lv / rv, t
    raise ComputeError(f"disallowed syntax: {ast.dump(node)[:80]}")


def _quarter(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3


def compute(con: duckdb.DuckDBPyConnection, expression: str,
            inputs: dict[str, str], as_of: date) -> Computation:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ComputeError(f"unparseable expression: {exc}") from exc
    used = _names(tree)
    if used != set(inputs):
        raise ComputeError(
            f"expression names {sorted(used)} must exactly match "
            f"inputs {sorted(inputs)}")

    rows: dict[str, tuple] = {}
    for var, fact_id in inputs.items():
        row = con.execute(
            "SELECT value, period_type, period_end, filed_date, cik, "
            "period_start FROM fact WHERE fact_id = ?", [fact_id]).fetchone()
        if row is None:
            raise ComputeError(f"no such fact: {fact_id} (input {var!r})")
        if row[3] > as_of:
            raise ComputeError(
                f"input {var!r} ({fact_id}) filed after as_of "
                f"({row[3]} > {as_of})")
        rows[var] = row

    ciks = {r[4] for r in rows.values()}
    if len(ciks) > 1:
        quarters = {_quarter(r[2]) for r in rows.values()}
        if len(quarters) > 1:
            raise ComputeError(
                "cross-company inputs must share a calendar quarter "
                f"(spec §4.8); got period_ends in quarters {sorted(quarters)}")
        lengths = {(r[2] - r[5]).days // 30 for r in rows.values()
                   if r[1] == "duration" and r[5] is not None}
        if len(lengths) > 1:
            raise ComputeError(
                "cross-company duration inputs must have equal length; "
                f"got ~{sorted(lengths)} months")

    values = {v: float(r[0]) for v, r in rows.items()}
    types = {v: r[1] for v, r in rows.items()}
    result, _ = _eval(tree, values, types)

    key = expression + "|" + json.dumps(dict(sorted(inputs.items()))) + \
        f"|{as_of}"
    derivation_id = "D-" + hashlib.sha256(key.encode()).hexdigest()[:16]
    con.execute("INSERT OR REPLACE INTO derivation VALUES (?,?,?,?,?)",
                [derivation_id, expression,
                 json.dumps(dict(sorted(inputs.items()))), result, as_of])
    return Computation(derivation_id=derivation_id, expression=expression,
                       inputs=inputs, values=values, value=result,
                       as_of=as_of)


def recompute(con: duckdb.DuckDBPyConnection,
              derivation_id: str) -> Computation:
    row = con.execute(
        "SELECT expression, inputs_json, as_of FROM derivation "
        "WHERE derivation_id = ?", [derivation_id]).fetchone()
    if row is None:
        raise ComputeError(f"no such derivation: {derivation_id}")
    return compute(con, row[0], json.loads(row[1]), row[2])
