### Task 4: `compute` — deterministic arithmetic over fact IDs

Spec §6: "The model does no arithmetic." Every derived number gets a persisted derivation record (§5 lineage) that guardrails and the eval recompute independently.

**Type rule (refines spec §4.4/§6, and record this in the spec):** the blanket "duration and instant never mix" would make the spec's own memo sections 5–6 uncomputable — asset turnover is revenue (duration) ÷ assets (instant); days-inventory is inventory (instant) ÷ COGS (duration) × days. The enforceable invariant is: **`+` and `-` require identical `period_type` among their operands; `*` and `/` are unrestricted.** Additive mixing is the modeling bug; ratios across types are the standard metrics.

**Cross-company guard (spec §4.8):** if inputs span more than one cik, every pair of inputs must end in the same calendar quarter, and duration inputs must have equal length; otherwise reject.

**Files:**
- Create: `src/edgar/tools/compute.py`
- Test: `tests/test_compute.py`
- Modify: `docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md` — §6 `compute` bullet: replace "Rejects duration/instant mixing" with "Rejects additive (+/−) mixing of duration and instant operands; ratios across types are permitted (asset turnover, days metrics require them)". One-line addition to §16 rev log.

**Interfaces:**
- Consumes: `fact` table, `Computation` DTO (Task 3).
- Produces:

```python
DERIVATION_DDL: str          # table derivation(derivation_id PK, expression,
                             #   inputs_json, value DOUBLE, as_of DATE)
create_derivation_table(con) -> None
class ComputeError(ValueError): ...
compute(con, expression: str, inputs: dict[str, str], as_of: date) -> Computation
recompute(con, derivation_id: str) -> Computation      # from the stored record
```

`derivation_id` = `"D-" + sha256(expression|sorted(inputs)|as_of)[:16]` — content-addressed, so identical computations collapse to one row (`INSERT OR REPLACE`).

**Expression grammar:** Python `ast` parsed with `mode="eval"`; allowed nodes: `Expression, BinOp, UnaryOp, Add, Sub, Mult, Div, USub, Name, Constant(int|float), ParenthesizedExpr-implicit`. Anything else (calls, attributes, comparisons, strings) → `ComputeError`. Names must all appear in `inputs`; unused inputs are an error too (a cited input that did not participate is a lie in the lineage).

- [ ] **Step 1: Failing tests** — `tests/test_compute.py` (reuse `_db`/`_fact` helper; `_db` needs only `create_fact_table`):

```python
import pytest
from datetime import date
from edgar.tools.compute import (
    compute, recompute, create_derivation_table, ComputeError)
# ... _db/_fact helper ...

def _setup(tmp_path):
    con = _db(tmp_path)
    create_derivation_table(con)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    _fact(con, fact_id="inv", field="inventory", value=30.0,
          ptype="instant", pstart=None)
    return con

def test_margin_with_derivation_record(tmp_path):
    con = _setup(tmp_path)
    c = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"}, date(2023, 6, 1))
    assert c.value == pytest.approx(0.4)
    assert c.values == {"gp": 40.0, "rev": 100.0}
    assert c.derivation_id.startswith("D-")
    again = recompute(con, c.derivation_id)
    assert again.value == pytest.approx(0.4)

def test_rejects_additive_type_mixing_but_allows_ratio(tmp_path):
    con = _setup(tmp_path)
    with pytest.raises(ComputeError, match="period_type"):
        compute(con, "rev + inv", {"rev": "rev", "inv": "inv"}, date(2023, 6, 1))
    days = compute(con, "inv / rev * 91", {"inv": "inv", "rev": "rev"},
                   date(2023, 6, 1))
    assert days.value == pytest.approx(27.3)

def test_rejects_input_filed_after_as_of(tmp_path):
    con = _setup(tmp_path)
    with pytest.raises(ComputeError, match="filed after as_of"):
        compute(con, "rev * 1", {"rev": "rev"}, date(2023, 4, 1))

def test_rejects_unsafe_and_unknown(tmp_path):
    con = _setup(tmp_path)
    for expr, inputs in [
        ("__import__('os')", {}),
        ("rev.value", {"rev": "rev"}),
        ("rev + missing", {"rev": "rev"}),          # name not in inputs
        ("rev", {"rev": "rev", "gp": "gp"}),        # unused input
    ]:
        with pytest.raises(ComputeError):
            compute(con, expr, inputs, date(2023, 6, 1))
    with pytest.raises(ComputeError, match="no such fact"):
        compute(con, "x * 1", {"x": "nope"}, date(2023, 6, 1))

def test_cross_company_calendar_guard(tmp_path):
    con = _setup(tmp_path)
    _fact(con, fact_id="peer_rev", cik=2, field="revenue", value=50.0,
          pstart=date(2023, 4, 1), pend=date(2023, 6, 30))  # different quarter
    with pytest.raises(ComputeError, match="calendar"):
        compute(con, "rev / peer", {"rev": "rev", "peer": "peer_rev"},
                date(2023, 9, 1))

def test_division_by_zero_is_compute_error(tmp_path):
    con = _setup(tmp_path)
    _fact(con, fact_id="z", field="capex", value=0.0)
    with pytest.raises(ComputeError, match="division by zero"):
        compute(con, "rev / z", {"rev": "rev", "z": "z"}, date(2023, 6, 1))
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/tools/compute.py`:

```python
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
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
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

    key = expression + "|" + json.dumps(dict(sorted(inputs.items()))) + f"|{as_of}"
    derivation_id = "D-" + hashlib.sha256(key.encode()).hexdigest()[:16]
    con.execute("INSERT OR REPLACE INTO derivation VALUES (?,?,?,?,?)",
                [derivation_id, expression,
                 json.dumps(dict(sorted(inputs.items()))), result, as_of])
    return Computation(derivation_id=derivation_id, expression=expression,
                       inputs=inputs, values=values, value=result, as_of=as_of)


def recompute(con: duckdb.DuckDBPyConnection, derivation_id: str) -> Computation:
    row = con.execute(
        "SELECT expression, inputs_json, as_of FROM derivation "
        "WHERE derivation_id = ?", [derivation_id]).fetchone()
    if row is None:
        raise ComputeError(f"no such derivation: {derivation_id}")
    return compute(con, row[0], json.loads(row[1]), row[2])
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_compute.py -q` → PASS (note `27.3 = 30/100*91`); then full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/tools/compute.py tests/test_compute.py \
  docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md
git commit -m "feat(tools): compute with derivation records; additive type-mixing rule

Spec §6 refined: +/- require like period_type, ratios cross types
(asset turnover and days metrics are duration/instant by definition).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

