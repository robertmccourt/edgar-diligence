from dataclasses import dataclass, astuple
import duckdb

CANONICAL_FIELDS: tuple[str, ...] = (
    "revenue", "cost_of_revenue", "gross_profit", "operating_income",
    "net_income", "total_assets", "total_liabilities",
    "stockholders_equity", "operating_cash_flow", "capex",
)


@dataclass(frozen=True)
class MappingRule:
    mapping_rule_id: str
    source_tag: str
    taxonomy: str
    canonical_field: str
    sign_convention: int
    scale: float
    method: str
    confidence: float
    priority: int
    rationale: str


def _r(n: int, tag: str, field: str, priority: int, rationale: str,
       sign: int = 1) -> MappingRule:
    return MappingRule(
        mapping_rule_id=f"MR-{n:04d}", source_tag=tag, taxonomy="us-gaap",
        canonical_field=field, sign_convention=sign, scale=1.0,
        method="deterministic", confidence=1.0, priority=priority,
        rationale=rationale,
    )


SEED_RULES: tuple[MappingRule, ...] = (
    _r(1, "RevenueFromContractWithCustomerExcludingAssessedTax", "revenue", 1,
       "ASC 606 primary revenue element; preferred post-2018."),
    _r(2, "RevenueFromContractWithCustomerIncludingAssessedTax", "revenue", 2,
       "ASC 606 variant including assessed tax."),
    _r(3, "Revenues", "revenue", 3, "Generic total revenue element."),
    _r(4, "SalesRevenueNet", "revenue", 4, "Pre-ASC-606 element; legacy filings."),
    _r(5, "CostOfRevenue", "cost_of_revenue", 1, "Total cost of revenue."),
    _r(6, "CostOfGoodsAndServicesSold", "cost_of_revenue", 2,
       "Combined goods and services cost."),
    _r(7, "CostOfGoodsSold", "cost_of_revenue", 3, "Goods-only cost; legacy."),
    _r(8, "GrossProfit", "gross_profit", 1, "Reported gross profit."),
    _r(9, "OperatingIncomeLoss", "operating_income", 1,
       "Standard operating income element."),
    _r(10, "NetIncomeLoss", "net_income", 1,
        "Net income attributable to the parent."),
    _r(11, "ProfitLoss", "net_income", 2,
        "Includes noncontrolling interests; used when NetIncomeLoss absent."),
    _r(12, "Assets", "total_assets", 1, "Total assets."),
    _r(13, "Liabilities", "total_liabilities", 1, "Total liabilities."),
    _r(14, "StockholdersEquity", "stockholders_equity", 1,
        "Parent-only stockholders equity."),
    _r(15, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "stockholders_equity", 2, "Total equity including NCI."),
    _r(16, "NetCashProvidedByUsedInOperatingActivities",
        "operating_cash_flow", 1, "Operating cash flow."),
    _r(17, "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "operating_cash_flow", 2, "Continuing-operations variant."),
    _r(18, "PaymentsToAcquirePropertyPlantAndEquipment", "capex", 1,
        "Capex reported as a positive payment (outflow); normalized positive."),
    _r(19, "PaymentsToAcquireProductiveAssets", "capex", 2,
        "Broader productive-asset purchases."),
)

_BY_TAG: dict[str, list[MappingRule]] = {}
for _rule in SEED_RULES:
    _BY_TAG.setdefault(_rule.source_tag, []).append(_rule)


def rules_for_tag(tag: str) -> list[MappingRule]:
    return sorted(_BY_TAG.get(tag, []), key=lambda r: r.priority)


MAPPING_DDL = """
CREATE TABLE IF NOT EXISTS mapping_rule (
    mapping_rule_id VARCHAR PRIMARY KEY,
    source_tag VARCHAR,
    taxonomy VARCHAR,
    canonical_field VARCHAR,
    sign_convention INTEGER,
    scale DOUBLE,
    method VARCHAR,
    confidence DOUBLE,
    priority INTEGER,
    rationale VARCHAR
);
"""


def create_mapping_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MAPPING_DDL)


def seed_mapping_rules(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("DELETE FROM mapping_rule WHERE method = 'deterministic'")
    con.executemany(
        "INSERT INTO mapping_rule VALUES (?,?,?,?,?,?,?,?,?,?)",
        [list(astuple(r)) for r in SEED_RULES],
    )
    return len(SEED_RULES)
