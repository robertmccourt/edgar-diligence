import json
from datetime import date, datetime

from edgar.agent.ledger import LedgerEntry
from edgar.narrative.store import search_spans
from edgar.tools.compute import ComputeError, compute
from edgar.tools.facts_tools import get_facts, list_available_facts
from edgar.tools.peers import get_peer_set

TOOL_DEFS: list[dict] = [
    {"name": "get_facts",
     "description": "Canonical financial facts for one company and period "
                    "window. Missing fields come back with a status code — "
                    "cite fact_id values verbatim.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"},
         "fields": {"type": "array", "items": {"type": "string"}},
         "period_start": {"type": "string", "description": "YYYY-MM-DD"},
         "period_end": {"type": "string", "description": "YYYY-MM-DD"}},
      "required": ["cik", "fields", "period_start", "period_end"]}},
    {"name": "search_filings",
     "description": "Search 10-K narrative (Items 1, 1A, 7). Returns spans "
                    "with span_id to cite.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}, "query": {"type": "string"},
         "items": {"type": "array", "items": {"type": "string"}}},
      "required": ["cik", "query"]}},
    {"name": "compute",
     "description": "The ONLY way to derive a number. Expression over "
                    "variables bound to fact_ids; returns value + "
                    "derivation_id to cite. + and - require like period "
                    "types; ratios may mix.",
     "input_schema": {"type": "object", "properties": {
         "expression": {"type": "string"},
         "inputs": {"type": "object",
                    "additionalProperties": {"type": "string"}}},
      "required": ["expression", "inputs"]}},
    {"name": "get_peer_set",
     "description": "Comparable companies by SIC, with the selection rule.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}, "min_peers": {"type": "integer"}},
      "required": ["cik"]}},
    {"name": "list_available_facts",
     "description": "Coverage map: which fields exist for which periods, "
                    "with status codes. Consult before claiming absence.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}},
      "required": ["cik"]}},
]


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def dispatch_tool(con, name, args, *, as_of, embedder, retrieval_k):
    try:
        if name == "get_facts":
            r = get_facts(con, int(args["cik"]), list(args["fields"]),
                          _d(args["period_start"]), _d(args["period_end"]),
                          as_of)
            entries = [LedgerEntry("fact", f.fact_id,
                                   f"{f.canonical_field} {f.period_end} "
                                   f"{f.unit} {f.value:g} "
                                   f"(filed {f.filed_date})", "")
                       for f in r.facts]
            return r.model_dump_json(), entries
        if name == "search_filings":
            hits = search_spans(con, args["query"], int(args["cik"]), as_of,
                                k=retrieval_k, embedder=embedder,
                                items=args.get("items"))
            entries = [LedgerEntry("span", h.span_id,
                                   f"{h.item} {h.accession}: "
                                   f"{h.text[:120]}", "",
                                   payload=h.text)
                       for h in hits]
            return json.dumps([h.model_dump(mode="json") for h in hits]), \
                entries
        if name == "compute":
            c = compute(con, args["expression"], dict(args["inputs"]),
                        as_of)
            entry = LedgerEntry("derivation", c.derivation_id,
                                f"{c.expression} = {c.value:g} "
                                f"inputs {c.inputs}", "")
            return c.model_dump_json(), [entry]
        if name == "get_peer_set":
            ps = get_peer_set(con, int(args["cik"]), as_of,
                              min_peers=int(args.get("min_peers", 10)))
            return ps.model_dump_json(), [LedgerEntry(
                "note", "", f"peer set ({len(ps.peers)}): "
                f"{ps.selection_rule}", "")]
        if name == "list_available_facts":
            rep = list_available_facts(con, int(args["cik"]), as_of)
            gist = "; ".join(
                f"{e.period_end}: " + ",".join(
                    f"{k}={v}" for k, v in sorted(e.statuses.items())
                    if v != "AVAILABLE")
                for e in rep.entries[:8]) or "all AVAILABLE"
            return rep.model_dump_json(), [LedgerEntry(
                "coverage", "", f"coverage: {gist}", "")]
        return json.dumps({"error": f"unknown tool {name}"}), []
    except (ComputeError, ValueError, KeyError, TypeError) as exc:
        return json.dumps({"error": str(exc)}), []
