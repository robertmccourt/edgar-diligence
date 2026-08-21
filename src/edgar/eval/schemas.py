from pydantic import BaseModel, field_validator

CLAIM_TYPES = ("NUMERIC", "DERIVED", "ATTRIBUTED", "INFERENTIAL",
               "UNSUPPORTED")
VERDICT_STATUSES = ("SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED",
                    "CONTRADICTED")


class RawClaim(BaseModel):
    claim_text: str
    claim_type: str
    citations: list[str] = []
    claimed_value: float | None = None

    @field_validator("claim_type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in CLAIM_TYPES:
            raise ValueError(f"claim_type must be one of {CLAIM_TYPES}")
        return v


class Decomposition(BaseModel):
    claims: list[RawClaim]


class JudgeOpinion(BaseModel):
    status: str
    reason: str

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in VERDICT_STATUSES:
            raise ValueError(f"status must be one of {VERDICT_STATUSES}")
        return v


class Verdict(BaseModel):
    claim: RawClaim
    status: str
    reason: str

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in VERDICT_STATUSES:
            raise ValueError(f"status must be one of {VERDICT_STATUSES}")
        return v
