"""CLI: venv/bin/python -m edgar.agent.run --cik 320193 --as-of 2024-03-01
        [--question "..."] [--config v1]"""
import argparse
from datetime import date

from edgar.agent.graph import run_agent
from edgar.agent.agent_config import load_agent_config
from edgar.config import load_secrets_env


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cik", type=int, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--question", default=None)
    p.add_argument("--config", default="v1")
    a = p.parse_args()
    load_secrets_env()
    memo = run_agent(cik=a.cik, as_of=date.fromisoformat(a.as_of),
                     question=a.question,
                     config=load_agent_config(a.config))
    print(f"memo written: data/memos/ (session {memo.session_id}, "
          f"trace {memo.trace_id})")


if __name__ == "__main__":
    main()
