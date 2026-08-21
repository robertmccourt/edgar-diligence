from edgar.agent.ledger import EvidenceLedger, LedgerEntry


def _big_ledger(n=50):
    led = EvidenceLedger()
    for i in range(n):
        led.append(LedgerEntry(kind="fact", identifier=f"f{i:04d}",
                               gist=("revenue grew strongly " * 30),
                               section="growth",
                               payload="x" * 2000))
    return led


def test_compaction_never_drops_identifiers():
    led = _big_ledger()
    before = led.identifiers()
    freed = led.compact()
    assert freed > 0
    assert led.identifiers() == before      # THE inviolable rule (spec §7.2)
    assert led.size_chars() < 50 * 2000


def test_compaction_truncates_gists_and_drops_payloads():
    led = _big_ledger()
    led.compact()
    assert all(e.payload == "" for e in led.entries)
    assert all(len(e.gist) <= 200 for e in led.entries)


def test_render_leads_with_identifier_and_filters_by_section():
    led = EvidenceLedger()
    led.append(LedgerEntry("fact", "fA", "rev FY23", "growth"))
    led.append(LedgerEntry("span", "sB", "mgmt on freight", "management"))
    out = led.render(section="growth")
    assert out.splitlines() == ["[fA] (fact, §growth) rev FY23"]
    assert "[sB]" in led.render()


def test_size_counts_gist_and_payload():
    led = EvidenceLedger()
    led.append(LedgerEntry("note", "", "abc", "growth", payload="12345"))
    assert led.size_chars() >= 8
