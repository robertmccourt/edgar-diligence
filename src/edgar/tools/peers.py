from datetime import date

import duckdb

from edgar.tools.schemas import Peer, PeerSet

_PEER_SQL = """
SELECT c.cik, c.name, c.sic, c.fiscal_year_end_month
FROM company c
JOIN (SELECT cik, count(*) AS n FROM fact
      WHERE filed_date <= ? GROUP BY cik) f ON f.cik = c.cik
WHERE c.eligibility_status = 'eligible' AND c.cik <> ? AND {predicate}
ORDER BY f.n DESC, c.cik
LIMIT ?
"""


def get_peer_set(con: duckdb.DuckDBPyConnection, cik: int, as_of: date,
                 min_peers: int = 10) -> PeerSet:
    """SIC-prefix peers, widening to sector when thin (spec §6).

    Known limitation: `company` is a latest-snapshot dimension (spec §4.7);
    sic/sector reflect today, not as_of. Facts visibility IS as-of-enforced.
    """
    row = con.execute(
        "SELECT sic, sector FROM company WHERE cik = ?", [cik]).fetchone()
    if row is None:
        raise ValueError(f"unknown cik {cik}")
    sic, sector = row
    cap = max(min_peers, 15)
    prefix = (str(sic).strip() or "??")[:2]
    rows = con.execute(_PEER_SQL.format(predicate="substr(c.sic, 1, 2) = ?"),
                       [as_of, cik, prefix, cap]).fetchall()
    rule = (f"eligible companies with 2-digit SIC prefix {prefix!r}, "
            f"≥1 fact filed on or before {as_of}, top {cap} by fact count")
    if len(rows) < min_peers:
        rows = con.execute(_PEER_SQL.format(predicate="c.sector = ?"),
                           [as_of, cik, sector, cap]).fetchall()
        rule = (f"SIC prefix {prefix!r} yielded <{min_peers}; widened to "
                f"sector {sector!r}, ≥1 fact filed on or before {as_of}, "
                f"top {cap} by fact count")
    peers = [Peer(cik=r[0], name=r[1], sic=r[2], fiscal_year_end_month=r[3])
             for r in rows]
    return PeerSet(cik=cik, as_of=as_of, peers=peers, selection_rule=rule)
