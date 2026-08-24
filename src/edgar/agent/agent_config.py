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
    # Seconds an OpenRouter call waits out free-pool saturation before
    # failing. Policy: wait for the pinned high-quality model, don't
    # downgrade. Default matches the pre-existing quick-fail behavior;
    # free configs set this high and run as monitored long jobs.
    llm_patience_s: int = 35
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
