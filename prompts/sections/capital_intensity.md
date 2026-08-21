# Section: Capital intensity

Question: how much must the company spend on long-lived assets to run and
grow, and how efficiently do assets generate revenue?

Fields: `capex`, `revenue` (durations); `total_assets` (instant).
Latest 8 quarters.

Computations (via `compute`; the turnover ratio mixes duration and
instant by design — that is allowed for ratios):
- Capex intensity: `capex / revenue` per period.
- Asset turnover:  `revenue / total_assets` using the quarter-end
  total_assets for the same quarter.

State the trend across periods, citing derivations. High and rising capex
intensity with flat revenue is the pattern to flag. If capex is missing,
report the status code.
