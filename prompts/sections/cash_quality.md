# Section: Cash quality

Question: does reported profit turn into actual cash?

Fields: `net_income`, `operating_cash_flow`, `capex` (durations).
Latest 8 quarters.

Computations (via `compute`, citing derivation_ids):
- Accrual gap: `operating_cash_flow - net_income` per period — a
  persistently negative gap (cash below profit) is the classic warning.
- Free cash flow: `operating_cash_flow - capex` per period.
- FCF conversion: `(operating_cash_flow - capex) / net_income` where
  net_income is positive.

State whether the gap is widening or narrowing across periods, citing the
per-period derivations. If any field is missing, report its status code
and compute what remains.
