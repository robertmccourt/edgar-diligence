# Section: Working capital

Question: how much cash is trapped in the operating cycle, and is the
trend improving or worsening?

Fields: `inventory`, `accounts_receivable`, `accounts_payable` (instants);
`revenue`, `cost_of_revenue` (durations). Latest 4-8 quarters.

Computations (via `compute`; instant/duration ratios are expected here):
- Days inventory:  `inventory / cost_of_revenue * 91`
- Days receivable: `accounts_receivable / revenue * 91`
- Days payable:    `accounts_payable / cost_of_revenue * 91`
Use quarter-aligned inputs (same period_end for the instant and the
quarter the duration covers). Compute for at least two periods and state
the direction of travel, citing every derivation.

If `cost_of_revenue` is missing (common: ~half of filers), say so with its
status code and compute only days receivable; do not substitute revenue
into the inventory/payable formulas.
