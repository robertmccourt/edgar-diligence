# Task 3 report — controller-transcribed (see ruling in ledger)

Status: DONE. Commit 157d552. 150 tests pass (4 new).

Changes: src/edgar/tools/__init__.py (empty), src/edgar/tools/schemas.py (all 9 DTO models from the Interfaces block, frozen), src/edgar/tools/facts_tools.py (get_facts with §4.6 missing statuses; list_available_facts), tests/test_facts_tools.py (4 tests per brief).

Deviation from brief: the test `_db` helper needed `create_mapping_table(con)` in addition to `init_schema(con)` — coverage_map queries mapping_rule for its mapped-tags set. The brief's own note anticipated the raw tables but missed mapping_rule. One-line helper addition; no production code deviated.
