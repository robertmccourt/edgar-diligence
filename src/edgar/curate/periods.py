from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class PeriodType(StrEnum):
    INSTANT = "instant"
    DURATION = "duration"


@dataclass(frozen=True)
class Period:
    period_type: PeriodType
    start: date | None
    end: date


def parse_yyyymmdd(s: str) -> date:
    s = s.strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"expected YYYYMMDD, got {s!r}")
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _minus_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)


def parse_period(ddate: str, qtrs: str) -> Period:
    end = parse_yyyymmdd(ddate)
    try:
        n = int(str(qtrs).strip())
    except ValueError as exc:
        raise ValueError(f"qtrs not an integer: {qtrs!r}") from exc
    if n < 0:
        raise ValueError(f"qtrs must be non-negative, got {n}")
    if n == 0:
        return Period(PeriodType.INSTANT, None, end)
    return Period(PeriodType.DURATION, _minus_months(end, n * 3 - 1), end)
