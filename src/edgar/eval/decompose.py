from edgar.agent.llm import LLMClient
from edgar.eval.schemas import Decomposition, RawClaim

_SYSTEM = ("You are a claim auditor for financial memos. "
           "You are precise and literal.")

_PROMPT = """Decompose the memo below into ATOMIC claims — one checkable \
assertion each; split compound sentences. For each claim:
- copy any [bracketed] citation identifiers into `citations` VERBATIM
- set claim_type: NUMERIC (states a specific figure), DERIVED (states a \
computed quantity: growth, margin, ratio, delta), ATTRIBUTED (reports what \
management or the filing SAYS), INFERENTIAL (an interpretation or \
judgment), UNSUPPORTED (carries no citation — regardless of content)
- for NUMERIC/DERIVED set claimed_value to the number as written (e.g. \
"$2.1B" -> 2.1; "240 bps" -> 240; "12%" -> 0.12)
Skip headings, the as-of banner, and status-code lines. Do not paraphrase.

MEMO:
{memo}
"""


def decompose(llm: LLMClient, memo_markdown: str) -> list[RawClaim]:
    out = llm.parse_structured(system=_SYSTEM,
                               prompt=_PROMPT.format(memo=memo_markdown),
                               output_model=Decomposition)
    return out.claims
