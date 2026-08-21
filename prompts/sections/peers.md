# Section: Peer positioning

Question: how do this company's economics compare to similar companies?

Tools: `get_peer_set` first — it returns the peer list AND the selection
rule; quote the rule in the section so the reader knows how peers were
chosen. Then `get_facts` for 2-3 peers and the subject, and `compute` one
or two comparable ratios (gross margin and capex intensity are good
defaults).

Calendar alignment is enforced by `compute`: cross-company inputs must
share a calendar quarter. If a peer's fiscal calendar does not align
(compute rejects it), say so rather than forcing the comparison — note
the peer's fiscal_year_end_month from the peer set.

Keep it to 2-3 peers and 1-2 ratios, every number cited. If the peer set
is empty, report that with the selection rule that produced it.
