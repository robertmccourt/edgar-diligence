# Section: What could not be answered

Question: what did the store fail to provide for this memo?

Method: enumerate every non-AVAILABLE status you encountered in this run —
from the coverage map loaded at the start and from every get_facts call
that returned a missing-field status. One line per field: the field, the
status code, and the §4.6 meaning:
- NOT_DISCLOSED — the company does not report this concept
- NOT_YET_FILED — it exists today but was not public at the cutoff
- UNMAPPED — the company reports something related our mapping misses
- AMBIGUOUS — candidate tags disagree; unresolved

NOT_DISCLOSED is a claim about the company; UNMAPPED is a claim about
this system. Never phrase the second as the first.

This section is the honesty surface. It is never empty if any other
section hit a gap, and it needs no citations — status codes are the
content.
