# Section: Growth

Question: is revenue growing, and is growth accelerating or decelerating?

Fields: `revenue` (duration). Pull 8-12 quarters via `get_facts`.

Computations (all via `compute`, citing derivation_ids):
- YoY growth for the latest 4 quarters: `(rev_t - rev_t4) / rev_t4`
- Sequential trend: compare consecutive YoY rates; state whether the
  growth rate is rising or falling — cite both derivations.

Watch for: fiscal-year boundaries (use period_start/period_end, not labels);
a revenue restatement visible as multiple filed versions of one period —
if present, mention it and let section 10 elaborate.

If revenue is missing or AMBIGUOUS for recent periods: report the status
code and write what can be said from older periods, or set the section to
status_code if nothing is producible.
