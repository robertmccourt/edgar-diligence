from dataclasses import dataclass

BASE_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets"


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    quarter: int

    def __post_init__(self) -> None:
        if not 1 <= self.quarter <= 4:
            raise ValueError(f"quarter must be 1-4, got {self.quarter}")

    @property
    def label(self) -> str:
        return f"{self.year}q{self.quarter}"

    def next(self) -> "Quarter":
        if self.quarter == 4:
            return Quarter(self.year + 1, 1)
        return Quarter(self.year, self.quarter + 1)


def enumerate_quarters(start: Quarter, end: Quarter) -> list[Quarter]:
    if start > end:
        raise ValueError(f"start {start.label} is after end {end.label}")
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = cur.next()
    return out


def archive_url(q: Quarter) -> str:
    return f"{BASE_URL}/{q.label}.zip"
