import os
from edgar.config import Settings, load_secrets_env


def test_model_defaults():
    s = Settings(_env_file=None)
    assert s.generation_model == "claude-opus-5"
    assert s.judge_model == "claude-sonnet-5"
    assert len(s.narrative_ciks) == 10 and 320193 in s.narrative_ciks


def test_load_secrets_env_sets_and_reports(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-test\n# comment\nEDGAR_DATA_DIR=x\n")
    set_names = load_secrets_env(env)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
    assert set_names == ["ANTHROPIC_API_KEY"]


def test_load_secrets_env_never_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-file\n")
    assert load_secrets_env(env) == []
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_load_secrets_env_missing_file_is_noop(tmp_path):
    assert load_secrets_env(tmp_path / "absent.env") == []
