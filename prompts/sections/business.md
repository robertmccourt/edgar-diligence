# Section: Business description

Question: what does this company do, and how does it make money?

Sources: `search_filings` over Item 1 only (`items=["Item 1"]`). Query for
the business model, segments as described in prose, and customer base.

Every statement here is ATTRIBUTED — it reports what the filing says, and
cites span_ids. Do not state numbers in this section unless they came from
`get_facts` with a fact_id; prefer to leave quantities to later sections.

If no Item 1 spans are available (narrative store may not cover this
company or period): set the section to status_code with a note that the
narrative store has no Item 1 text as of the cutoff.
