# Task 6 report — controller-transcribed (see Task 3 ruling)
Status: DONE (code). 162 tests pass (3 new).
Changes: narrative/store.py (DDL), narrative/fetch.py (fetch_narratives, verify_store, FilingDoc), tests (3), scripts/fetch_narratives.py, Makefile `narrative` target.
Deviation (ruled): plan's inline edgartools_fetcher dual-import flagged by the plan itself as "will not work as written"; adopted the plan's fallback — script vendors the fetch using edgartools only, library fetcher raises with explanation. fetch_narratives/verify_store remain the tested library surface.
Pending-human/stable-window: `make narrative` real SEC pull + verify_store enumeration (Step 5).
