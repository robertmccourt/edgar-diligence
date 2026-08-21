# Section: Balance sheet and leverage

Question: how much does the company owe, and how does its debt compare to
its equity and cash?

Fields: `long_term_debt`, `cash_and_equivalents`, `stockholders_equity`,
`total_liabilities` (instants). Latest 4-8 quarter-ends.

Computations (via `compute`, citing derivation_ids):
- Debt to equity: `long_term_debt / stockholders_equity`
- Net debt:       `long_term_debt - cash_and_equivalents`

Caveat you must state when citing long_term_debt: the field excludes
short-term borrowings by construction, and when the company files only
the noncurrent tag it also excludes current maturities — see the data
dictionary. Do not call it "total debt".

If long_term_debt is NOT_DISCLOSED, the company may simply carry no
long-term debt — say exactly that with the status code, and note
total_liabilities (which conflates debt with operating obligations) as
the only available aggregate, cited.
