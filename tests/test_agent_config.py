from edgar.agent.agent_config import load_agent_config


def test_v1_loads_with_pinned_models():
    cfg = load_agent_config("v1")
    assert cfg.generation_model == "claude-opus-5"
    assert cfg.judge_model == "claude-sonnet-5"
    assert cfg.config_version.startswith("v1+")
    assert len(cfg.config_version) == 11


def test_version_hash_moves_when_prompts_change(tmp_path):
    import shutil
    from pathlib import Path
    root = tmp_path
    shutil.copytree(Path("config"), root / "config")
    shutil.copytree(Path("prompts"), root / "prompts")
    v_before = load_agent_config("v1", root=root).config_version
    (root / "prompts" / "system.md").write_text("changed\n" * 20)
    assert load_agent_config("v1", root=root).config_version != v_before
