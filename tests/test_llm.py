import pytest
from pydantic import BaseModel

from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall


class _Out(BaseModel):
    answer: str


def test_fake_llm_plays_script_in_order():
    t1 = LLMTurn(text="", tool_calls=[ToolCall("t1", "get_facts",
                                               {"cik": 1})],
                 raw_content=[], usage_in=10, usage_out=5)
    t2 = LLMTurn(text="done", tool_calls=[], raw_content=[], usage_in=8,
                 usage_out=4)
    llm = FakeLLM(turns=[t1, t2], parsed=[_Out(answer="hi")])
    assert llm.tool_turn(system="s", messages=[],
                         tools=[]).tool_calls[0].name == "get_facts"
    assert llm.tool_turn(system="s", messages=[], tools=[]).text == "done"
    assert llm.parse_structured(system="s", prompt="p",
                                output_model=_Out).answer == "hi"


def test_fake_llm_raises_when_script_exhausted():
    llm = FakeLLM()
    with pytest.raises(AssertionError, match="script exhausted"):
        llm.tool_turn(system="s", messages=[], tools=[])


def test_fake_llm_records_calls_for_assertions():
    llm = FakeLLM(turns=[LLMTurn("x", [], [], 1, 1)])
    llm.tool_turn(system="SYS", messages=[{"role": "user", "content": "u"}],
                  tools=[{"name": "compute"}])
    assert llm.calls[0]["system"] == "SYS"
    assert llm.calls[0]["tools"][0]["name"] == "compute"
