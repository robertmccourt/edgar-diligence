# Section: Profitability

Question: how profitable is the company, and where in the cost stack is
pressure showing up?

Fields: `revenue`, `gross_profit`, `operating_income`, `net_income`
(durations). Latest 8 quarters.

Computations (via `compute`, citing derivation_ids):
- Gross margin:     `gross_profit / revenue`
- Operating margin: `operating_income / revenue`
- Net margin:       `net_income / revenue`
Compute each for at least the latest and year-ago quarters. Locate the
pressure: if gross margin is stable but operating margin fell, the issue
is operating costs, not input costs — state which layer moved, citing the
derivations for both periods.

If `gross_profit` is NOT_DISCLOSED (common), say so with the status code
and work with operating and net margins only. Do not derive gross profit
from revenue minus cost_of_revenue unless both fact_ids exist — and if
you do, that subtraction goes through `compute` like everything else.
