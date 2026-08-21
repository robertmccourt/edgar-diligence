### Task 10: Procedural memory + versioned agent config

Spec §7.4 (procedural tier) and §9.2 (one versioned object for everything that changes behavior). Prompts are files; the config names them and pins every knob; `config_version` stamps sessions, memos, and eval reports so metric changes are attributable.

**Files:**
- Create: `prompts/system.md`, `prompts/sections/*.md` (11 files), `config/versions/v1.yaml`, `src/edgar/memory/procedural.py`, `src/edgar/agent/__init__.py` (empty), `src/edgar/agent/agent_config.py`
- Test: `tests/test_procedural.py`, `tests/test_agent_config.py`

**Interfaces:**
- Produces:

```python
# edgar.memory.procedural
SECTIONS: tuple[tuple[int, str, str], ...]   # (number, slug, title) — 11 entries
load_system_prompt(prompts_dir: Path | None = None) -> str
load_rubric(slug: str, prompts_dir: Path | None = None) -> str

# edgar.agent.agent_config
class AgentConfig(BaseModel):
    name: str; generation_model: str; judge_model: str
    retrieval_k: int; max_tool_turns: int; max_repair_rounds: int
    context_budget_chars: int; compaction_threshold_chars: int
    recall_limit: int; prompts_sha: str; config_version: str  # e.g. "v1+3fa9c2d1"
load_agent_config(name: str = "v1", root: Path | None = None) -> AgentConfig
```

`SECTIONS` (slugs are also the rubric filenames and the memo section keys — every later task uses these exact slugs):

```python
SECTIONS = (
    (1, "business", "Business description"),
    (2, "growth", "Growth"),
    (3, "profitability", "Profitability"),
    (4, "cash_quality", "Cash quality"),
    (5, "capital_intensity", "Capital intensity"),
    (6, "working_capital", "Working capital"),
    (7, "leverage", "Balance sheet and leverage"),
    (8, "peers", "Peer positioning"),
    (9, "management", "Management's explanation and new risk factors"),
    (10, "reliability", "Data reliability flags"),
    (11, "unanswered", "What could not be answered"),
)
```

`config_version` = `f"{name}+{sha256(yaml_bytes + all prompt bytes)[:8]}"` — any edit to a prompt or knob changes the version string automatically.

- [ ] **Step 1: Failing tests**

`tests/test_procedural.py`:

```python
from edgar.memory.procedural import SECTIONS, load_system_prompt, load_rubric

def test_eleven_sections_with_stable_slugs():
    assert len(SECTIONS) == 11
    assert [s[1] for s in SECTIONS][:3] == ["business", "growth", "profitability"]

def test_every_section_has_a_nonempty_rubric():
    for _, slug, _ in SECTIONS:
        text = load_rubric(slug)
        assert len(text) > 100, f"rubric {slug} too thin"

def test_system_prompt_states_the_three_laws():
    text = load_system_prompt()
    for needle in ("cite", "as_of", "compute"):
        assert needle in text.lower()
```

`tests/test_agent_config.py`:

```python
from edgar.agent.agent_config import load_agent_config

def test_v1_loads_with_pinned_models():
    cfg = load_agent_config("v1")
    assert cfg.generation_model == "claude-opus-5"
    assert cfg.judge_model == "claude-sonnet-5"
    assert cfg.config_version.startswith("v1+") and len(cfg.config_version) == 11

def test_version_hash_moves_when_prompts_change(tmp_path):
    import shutil
    from pathlib import Path
    root = tmp_path
    shutil.copytree(Path("config"), root / "config")
    shutil.copytree(Path("prompts"), root / "prompts")
    v_before = load_agent_config("v1", root=root).config_version
    (root / "prompts" / "system.md").write_text("changed\n" * 20)
    assert load_agent_config("v1", root=root).config_version != v_before
```

- [ ] **Step 2: Run to verify failure** — ImportError / missing files expected.

- [ ] **Step 3: Implement**

`config/versions/v1.yaml`:

```yaml
generation_model: claude-opus-5
judge_model: claude-sonnet-5
retrieval_k: 8
max_tool_turns: 12          # per section; hard stop on the retrieve loop
max_repair_rounds: 2        # guardrail repair attempts before downgrade-all
context_budget_chars: 60000 # ledger render budget fed to the memo writer
compaction_threshold_chars: 45000
recall_limit: 5             # episodic conclusions loaded per run
```

`prompts/system.md` — the agent's constitution, verbatim:

```markdown
# Diligence analyst — operating rules

You are a financial diligence analyst writing about ONE public company as
of a FIXED cutoff date. You work only from tool results. The tools enforce
the cutoff in the database; you will simply never see later information.

## The three laws

1. **Every numeric or attributed statement carries a citation.** A citation
   is an identifier copied VERBATIM from a tool result: a `fact_id`, a
   `span_id`, or a `derivation_id`. Never invent, abbreviate, or repair an
   identifier. A statement you cannot cite is a hypothesis and must be
   labeled as one.
2. **You do no arithmetic.** Every growth rate, margin, ratio, delta, and
   difference — however trivial — goes through the `compute` tool over
   fact_ids, and you cite the returned derivation_id. This includes percent
   changes you could do in your head.
3. **What the tools cannot show does not exist.** When the coverage map
   reports a field as NOT_DISCLOSED, NOT_YET_FILED, UNMAPPED, or AMBIGUOUS,
   report that status code. Never estimate, interpolate, or fill from
   general knowledge. "I cannot answer this from the store" is a correct
   and expected answer.

## Style

Terse and factual. No superlatives, no filler. One assertion per claim.
Prefer exact figures with units over rounded prose. Distinguish what the
data shows from what management says (attributed) and from what you infer
(inferential — cite the premises). Value-creation ideas go in the
hypotheses list, labeled, never asserted as fact.
```

`prompts/sections/growth.md` — the pattern every rubric follows, verbatim:

```markdown
# Section: Growth

Question: is revenue growing, and is growth accelerating or decelerating?

Fields: `revenue` (duration). Pull 8-12 quarters via `get_facts`.

Computations (all via `compute`, citing derivation_ids):
- YoY growth for the latest 4 quarters: `(rev_t - rev_t4) / rev_t4`
- Sequential trend: compare consecutive YoY rates; state whether the
  growth rate is rising or falling — cite both derivations.

Watch for: fiscal-year boundaries (use period_start/period_end, not labels);
a revenue restatement visible as multiple filed versions of one period —
if present, mention it and let section 10 elaborate.

If revenue is missing or AMBIGUOUS for recent periods: report the status
code and write what can be said from older periods, or set the section to
status_code if nothing is producible.
```

`prompts/sections/working_capital.md` — verbatim (the days-metrics section that needs Task 1's fields):

```markdown
# Section: Working capital

Question: how much cash is trapped in the operating cycle, and is the
trend improving or worsening?

Fields: `inventory`, `accounts_receivable`, `accounts_payable` (instants);
`revenue`, `cost_of_revenue` (durations). Latest 4-8 quarters.

Computations (via `compute`; instant/duration ratios are expected here):
- Days inventory:  `inventory / cost_of_revenue * 91`
- Days receivable: `accounts_receivable / revenue * 91`
- Days payable:    `accounts_payable / cost_of_revenue * 91`
Use quarter-aligned inputs (same period_end for the instant and the
quarter the duration covers). Compute for at least two periods and state
the direction of travel, citing every derivation.

If `cost_of_revenue` is missing (common: ~half of filers), say so with its
status code and compute only days receivable; do not substitute revenue
into the inventory/payable formulas.
```

The remaining nine rubrics follow the growth.md pattern exactly — question, fields (exact canonical names), computations (exact `compute` expressions), missing-data instruction. Their content specs: `business.md` (search_filings Item 1, attributed claims only, no numbers without facts); `profitability.md` (gross/operating/net margins via compute from `gross_profit`,`operating_income`,`net_income`,`revenue`; where in the stack pressure sits); `cash_quality.md` (net_income vs operating_cash_flow divergence, FCF = `operating_cash_flow - capex`); `capital_intensity.md` (`capex / revenue`, asset turnover `revenue / total_assets`); `leverage.md` (`long_term_debt / stockholders_equity`, net debt `long_term_debt - cash_and_equivalents`; note the long_term_debt naming caveat from the data dictionary); `peers.md` (get_peer_set, compare 1-2 computed ratios across peers, calendar-aligned only); `management.md` (search_filings Items 7 and 1A, attributed claims with span_ids; new-vs-prior risk factors only if spans from two filings support it); `reliability.md` (restatement evidence: multiple filed versions of one figure surfaced in get_facts results; filing-lag observations; cite fact_ids of both versions); `unanswered.md` (enumerate every non-AVAILABLE status encountered this run with its code; this section is never empty if any other section hit a gap).

`prompts/sections/<slug>.md` — one per section; each states: what question the section answers (plain language), which canonical fields / tools to use (exact field names from Task 1's 15), which computations to run via `compute` (e.g. `working_capital.md`: days inventory = `inventory / cost_of_revenue * 91` on aligned quarters; days receivable = `accounts_receivable / revenue * 91`; days payable = `accounts_payable / cost_of_revenue * 91`; note the instant/duration ratio is expected), and what to do when inputs are missing (emit the status code, move on). `reliability.md` instructs calling `get_facts` at two different `as_of` dates is NOT available — instead cite `restatement` evidence via the fact table's multiple filed versions surfaced in tool results. `unanswered.md` instructs summarizing every non-AVAILABLE status encountered.

`src/edgar/memory/procedural.py`:

```python
from pathlib import Path

SECTIONS: tuple[tuple[int, str, str], ...] = (
    (1, "business", "Business description"),
    (2, "growth", "Growth"),
    (3, "profitability", "Profitability"),
    (4, "cash_quality", "Cash quality"),
    (5, "capital_intensity", "Capital intensity"),
    (6, "working_capital", "Working capital"),
    (7, "leverage", "Balance sheet and leverage"),
    (8, "peers", "Peer positioning"),
    (9, "management", "Management's explanation and new risk factors"),
    (10, "reliability", "Data reliability flags"),
    (11, "unanswered", "What could not be answered"),
)

_DEFAULT = Path("prompts")


def load_system_prompt(prompts_dir: Path | None = None) -> str:
    return ((prompts_dir or _DEFAULT) / "system.md").read_text()


def load_rubric(slug: str, prompts_dir: Path | None = None) -> str:
    if slug not in {s[1] for s in SECTIONS}:
        raise KeyError(f"unknown section slug: {slug}")
    return ((prompts_dir or _DEFAULT) / "sections" / f"{slug}.md").read_text()
```

`src/edgar/agent/agent_config.py`:

```python
import hashlib
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    generation_model: str
    judge_model: str
    retrieval_k: int
    max_tool_turns: int
    max_repair_rounds: int
    context_budget_chars: int
    compaction_threshold_chars: int
    recall_limit: int
    prompts_sha: str
    config_version: str


def load_agent_config(name: str = "v1", root: Path | None = None) -> AgentConfig:
    root = root or Path(".")
    yaml_path = root / "config" / "versions" / f"{name}.yaml"
    raw = yaml_path.read_bytes()
    data = yaml.safe_load(raw)
    h = hashlib.sha256(raw)
    for p in sorted((root / "prompts").rglob("*.md")):
        h.update(p.read_bytes())
    digest = h.hexdigest()[:8]
    return AgentConfig(name=name, prompts_sha=digest,
                       config_version=f"{name}+{digest}", **data)
```

- [ ] **Step 4: Run** — both test files then full suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts config src/edgar/memory/procedural.py src/edgar/agent \
  tests/test_procedural.py tests/test_agent_config.py
git commit -m "feat(agent): procedural rubrics + hash-versioned config (spec 9.2)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

