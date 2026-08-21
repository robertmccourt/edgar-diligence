from edgar.agent.llm import FakeLLM
from edgar.eval.decompose import decompose
from edgar.eval.schemas import Decomposition, RawClaim


def test_decompose_passes_markdown_and_returns_claims():
    scripted = Decomposition(claims=[
        RawClaim(claim_text="Revenue was $2.1B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=2.1)])
    llm = FakeLLM(parsed=[scripted])
    claims = decompose(llm, "# memo\n- Revenue was $2.1B [fA]")
    assert claims[0].citations == ["fA"]
    assert "Revenue was $2.1B [fA]" in llm.parse_calls[0]["prompt"]


def test_decompose_prompt_demands_atomicity_and_verbatim_ids():
    llm = FakeLLM(parsed=[Decomposition(claims=[])])
    decompose(llm, "x")
    p = llm.parse_calls[0]["prompt"].lower()
    assert "atomic" in p and "verbatim" in p
