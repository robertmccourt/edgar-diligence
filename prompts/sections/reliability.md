# Section: Data reliability flags

Question: how trustworthy are this company's reported figures — do they
restate, and how late do they file?

Method: `get_facts` results carry filed_date and accession per fact.
When the same figure (same field, same period_start/period_end, same
unit) appears with MULTIPLE filed versions in your evidence, that is a
restatement trail: cite BOTH fact_ids and state the values and filing
dates. The bitemporal store preserves every version precisely so this
section can exist.

Also observe filing lag: the gap between period_end and filed_date on
the facts you retrieved — flag a company whose lag is long or worsening,
citing the fact_ids whose dates show it.

No restatements observed in the evidence gathered is itself a statement
worth making — phrase it as scoped to the periods examined, not as a
blanket claim.
