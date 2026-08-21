from dataclasses import dataclass, field

_GIST_CAP = 200


@dataclass
class LedgerEntry:
    kind: str
    identifier: str
    gist: str
    section: str
    payload: str = ""


@dataclass
class EvidenceLedger:
    """Typed working memory (spec §7.1). The memo is written FROM this,
    which is what makes post-hoc auditing possible."""
    entries: list[LedgerEntry] = field(default_factory=list)

    def append(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def identifiers(self) -> set[str]:
        return {e.identifier for e in self.entries if e.identifier}

    def size_chars(self) -> int:
        return sum(len(e.gist) + len(e.payload) for e in self.entries)

    def compact(self) -> int:
        """Compress prose; NEVER compress identifiers (spec §7.2).
        A compaction that drops a citation is a bug, not a tradeoff."""
        before = self.size_chars()
        for e in self.entries:
            e.payload = ""
            if len(e.gist) > _GIST_CAP:
                e.gist = e.gist[:_GIST_CAP - 1] + "…"
        return before - self.size_chars()

    def render(self, section: str | None = None) -> str:
        rows = [e for e in self.entries
                if section is None or e.section == section]
        return "\n".join(
            f"[{e.identifier}] ({e.kind}, §{e.section}) {e.gist}"
            for e in rows)
