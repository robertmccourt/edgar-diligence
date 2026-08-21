from datetime import date
from pydantic import BaseModel, ConfigDict


class FactDTO(BaseModel):
    model_config = ConfigDict(frozen=True)
    fact_id: str
    cik: int
    canonical_field: str
    value: float
    unit: str
    period_type: str
    period_start: date | None
    period_end: date
    filed_date: date
    accession: str
    source_tag: str


class MissingField(BaseModel):
    model_config = ConfigDict(frozen=True)
    canonical_field: str
    status: str


class GetFactsResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    facts: list[FactDTO]
    missing: list[MissingField]


class CoverageEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    period_end: date
    statuses: dict[str, str]


class CoverageReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    cik: int
    as_of: date
    entries: list[CoverageEntry]


class SpanDTO(BaseModel):
    model_config = ConfigDict(frozen=True)
    span_id: str
    cik: int
    accession: str
    form: str
    item: str
    filed_date: date
    char_start: int
    char_end: int
    text: str


class Computation(BaseModel):
    model_config = ConfigDict(frozen=True)
    derivation_id: str
    expression: str
    inputs: dict[str, str]
    values: dict[str, float]
    value: float
    as_of: date


class Peer(BaseModel):
    model_config = ConfigDict(frozen=True)
    cik: int
    name: str
    sic: str
    fiscal_year_end_month: int | None


class PeerSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    cik: int
    as_of: date
    peers: list[Peer]
    selection_rule: str
