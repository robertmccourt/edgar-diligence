### Task 11: Evidence ledger + compaction

Spec §7.1–7.2. The ledger is the typed working memory; the memo is written FROM it. Compaction compresses prose and NEVER drops an identifier — the inviolable rule gets its own test.

**Files:**
- Create: `src/edgar/agent/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**

```python
@dataclass
class LedgerEntry:
    kind: str            # "fact" | "span" | "derivation" | "coverage" | "note"
    identifier: str      # fact_id / span_id / derivation_id / "" for notes
    gist: str            # what this establishes, agent-authored, one line
    section: str         # section slug it serves
    payload: str = ""    # raw tool JSON; dropped first under pressure

class EvidenceLedger:
    entries: list[LedgerEntry]
    def append(self, entry: LedgerEntry) -> None
    def identifiers(self) -> set[str]
    def size_chars(self) -> int
    def compact(self) -> int          # returns chars freed; NEVER drops identifiers
    def render(self, section: str | None = None) -> str   # prompt-ready block
```

`compact()` policy, in order: (1) drop all `payload`s; (2) truncate every `gist` to 200 chars. Identifiers and entry rows are never removed. `render()` emits one line per entry: `[{identifier}] ({kind}, §{section}) {gist}` — the identifier leads so the writer model copies it correctly.

- [ ] **Step 1: Failing tests** — `tests/test_ledger.py`:

```python
from edgar.agent.ledger import EvidenceLedger, LedgerEntry

def _big_ledger(n=50):
    led = EvidenceLedger()
    for i in range(n):
        led.append(LedgerEntry(kind="fact", identifier=f"f{i:04d}",
                               gist=("revenue grew strongly " * 30),
                               section="growth",
                               payload="x" * 2000))
    return led

def test_compaction_never_drops_identifiers():
    led = _big_ledger()
    before = led.identifiers()
    freed = led.compact()
    assert freed > 0
    assert led.identifiers() == before          # THE inviolable rule (spec §7.2)
    assert led.size_chars() < 50 * 2000

def test_compaction_truncates_gists_and_drops_payloads():
    led = _big_ledger()
    led.compact()
    assert all(e.payload == "" for e in led.entries)
    assert all(len(e.gist) <= 200 for e in led.entries)

def test_render_leads_with_identifier_and_filters_by_section():
    led = EvidenceLedger()
    led.append(LedgerEntry("fact", "fA", "rev FY23", "growth"))
    led.append(LedgerEntry("span", "sB", "mgmt on freight", "management"))
    out = led.render(section="growth")
    assert out.splitlines() == ["[fA] (fact, §growth) rev FY23"]
    assert "[sB]" in led.render()

def test_size_counts_gist_and_payload():
    led = EvidenceLedger()
    led.append(LedgerEntry("note", "", "abc", "growth", payload="12345"))
    assert led.size_chars() >= 8
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/agent/ledger.py`:

```python
from dataclasses import dataclass, field

_GIST_CAP = 200


@dataclass
class LedgerEntry:
    kind: str
    identifier: str
    gist: str
    section: str
    payload: str = ""


@dataclass
class EvidenceLedger:
    """Typed working memory (spec §7.1). The memo is written FROM this,
    which is what makes post-hoc auditing possible."""
    entries: list[LedgerEntry] = field(default_factory=list)

    def append(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def identifiers(self) -> set[str]:
        return {e.identifier for e in self.entries if e.identifier}

    def size_chars(self) -> int:
        return sum(len(e.gist) + len(e.payload) for e in self.entries)

    def compact(self) -> int:
        """Compress prose; NEVER compress identifiers (spec §7.2).
        A compaction that drops a citation is a bug, not a tradeoff."""
        before = self.size_chars()
        for e in self.entries:
            e.payload = ""
            if len(e.gist) > _GIST_CAP:
                e.gist = e.gist[:_GIST_CAP - 1] + "…"
        return before - self.size_chars()

    def render(self, section: str | None = None) -> str:
        rows = [e for e in self.entries
                if section is None or e.section == section]
        return "\n".join(
            f"[{e.identifier}] ({e.kind}, §{e.section}) {e.gist}"
            for e in rows)
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_ledger.py -q` → PASS; full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/agent/ledger.py tests/test_ledger.py
git commit -m "feat(agent): evidence ledger; compaction preserves identifiers verbatim

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

