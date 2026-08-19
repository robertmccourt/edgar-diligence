import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from edgar.config import get_settings

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


class RateLimiter:
    """SEC permits at most 10 requests/second. Default leaves headroom."""

    def __init__(self, max_per_second: float = 8.0) -> None:
        self._interval = 1.0 / max_per_second
        self._last = 0.0

    def acquire(self) -> None:
        wait = self._interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


_DEFAULT_LIMITER = RateLimiter()


CHUNK_BYTES = 1 << 20


def download_archive(
    q: Quarter,
    dest_dir: Path,
    client: httpx.Client | None = None,
    limiter: RateLimiter | None = None,
) -> Path:
    """Fetch one quarterly archive into `dest_dir`, atomically.

    The archive is streamed to a `.part` sibling and moved onto the final
    path with os.replace() only once the body is fully written. os.replace
    is atomic within a filesystem, so `{label}.zip` either does not exist
    or is a complete archive — never a truncated one.

    This matters because the cache is trusted on sight: `if dest.exists():
    return dest` never revalidates, so a single interruption during a
    direct write to `dest` would poison that quarter permanently, and a
    full build fetches ~29 archives of 100-200MB each. Any partial file is
    removed in the `finally`, so an interrupted run leaves the cache
    exactly as it found it and the next run simply re-fetches.

    Streaming rather than buffering `resp.content` also holds peak memory
    to one chunk instead of the whole archive.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{q.label}.zip"
    if dest.exists():
        return dest
    partial = dest.with_name(dest.name + ".part")

    (limiter or _DEFAULT_LIMITER).acquire()
    owns = client is None
    client = client or httpx.Client(
        headers={"User-Agent": get_settings().sec_user_agent},
        timeout=120.0,
        follow_redirects=True,
    )
    try:
        with client.stream("GET", archive_url(q)) as resp:
            resp.raise_for_status()
            with open(partial, "wb") as fh:
                for chunk in resp.iter_bytes(CHUNK_BYTES):
                    fh.write(chunk)
        os.replace(partial, dest)
    finally:
        partial.unlink(missing_ok=True)
        if owns:
            client.close()
    return dest
