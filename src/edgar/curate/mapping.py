from dataclasses import dataclass, astuple
import duckdb

CANONICAL_FIELDS: tuple[str, ...] = (
    "revenue", "cost_of_revenue", "gross_profit", "operating_income",
    "net_income", "total_assets", "total_liabilities",
    "stockholders_equity", "operating_cash_flow", "capex",
    "inventory", "accounts_receivable", "accounts_payable",
    "long_term_debt", "cash_and_equivalents",
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
    _r(20, "InventoryNet", "inventory", 1,
       "Net inventory at the balance sheet date; the dominant aggregate tag "
       "(4,123 of 11,119 ciks, 2019-2026 store)."),
    _r(21, "AccountsReceivableNetCurrent", "accounts_receivable", 1,
       "Net current trade receivables; dominant tag (5,476 ciks)."),
    _r(22, "ReceivablesNetCurrent", "accounts_receivable", 2,
       "Broader current receivables aggregate (incl. notes); used when the "
       "trade-specific tag is absent (842 ciks)."),
    _r(23, "AccountsPayableCurrent", "accounts_payable", 1,
       "Current accounts payable; dominant tag (6,116 ciks). The combined "
       "AccountsPayableAndAccruedLiabilitiesCurrent tag is deliberately NOT "
       "mapped: it conflates payables with accrued liabilities and would "
       "corrupt days-payable arithmetic."),
    _r(24, "AccountsPayableTradeCurrent", "accounts_payable", 2,
       "Trade-only payables; narrower, used when the aggregate is absent."),
    _r(25, "LongTermDebt", "long_term_debt", 1,
       "Total long-term debt including current maturities. Field is named "
       "long_term_debt, not total_debt: no single GAAP tag expresses "
       "ST+LT total debt for more than a few hundred filers; short-term "
       "borrowings are excluded by construction."),
    _r(26, "LongTermDebtAndCapitalLeaseObligations", "long_term_debt", 2,
       "Broader: includes finance-lease obligations; used when the pure "
       "debt total is absent."),
    _r(27, "LongTermDebtNoncurrent", "long_term_debt", 3,
       "Noncurrent portion only (3,097 ciks — the most common form). "
       "Understates by current maturities when the total tags are absent."),
    _r(28, "CashAndCashEquivalentsAtCarryingValue", "cash_and_equivalents", 1,
       "Unrestricted cash and equivalents; dominant tag (9,117 ciks)."),
    _r(29, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "cash_and_equivalents", 2,
        "Cash-flow-statement reconciliation total; overstates by restricted "
        "cash when the unrestricted tag is absent."),
    _r(30, "Cash", "cash_and_equivalents", 3,
       "Bare cash; understates by excluding equivalents. Legacy filers."),
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
