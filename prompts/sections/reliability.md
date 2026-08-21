# Section: Data reliability flags

Question: how trustworthy are this company's reported figures — do they
restate, and how late do they file?

Method: `get_facts` results carry filed_date and accession per fact. When
a figure looks like it may have been revised — or you simply want to
check — call `get_fact_history(cik, canonical_field, period_end,
period_start, period_type, unit)` for that exact figure. If it returns
MULTIPLE fact_ids, that is a restatement trail: cite ALL of them and
state the values and filing dates. The bitemporal store preserves every
version precisely so this section can exist; `get_fact_history` is the
only tool that surfaces more than the latest one.

Also observe filing lag: the gap between period_end and filed_date on
the facts you retrieved — flag a company whose lag is long or worsening,
citing the fact_ids whose dates show it.

No restatements observed in the evidence gathered is itself a statement
worth making — phrase it as scoped to the periods examined, not as a
blanket claim.
