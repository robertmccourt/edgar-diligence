# DERA Financial Statement Data Sets — Format Verification

**Date verified:** 2026-08-18
**Verified by:** Robert McCourt (rmmccourt01@comcast.net)
**Method:** Direct `curl` requests to sec.gov with a declared User-Agent
(`Robert McCourt rmmccourt01@comcast.net`), spaced out well under the 10
requests/second limit. No per-company API calls were used — only the bulk
quarterly archive and two documentation pages.

## Sources fetched

Both URLs named in the task brief return HTTP 301 redirects to new
locations under SEC's current site structure. The content was fetched from
the redirect targets:

| Requested URL | Result | Redirects to |
|---|---|---|
| `https://www.sec.gov/dera/data/financial-statement-data-sets.html` | 301 | `https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets` |
| `https://www.sec.gov/os/webmaster-faq#developers` | 301 | `https://www.sec.gov/about/webmaster-frequently-asked-questions` |

Both redirect targets returned HTTP 200 and were read in full. This is a
cosmetic finding, not a contradiction: the redirects work, and any later
task should either use the new canonical URLs for documentation links or
be aware old doc URLs will 301. It does **not** affect the data archive
URL template itself (see Q1 below), which is a separate, unredirected path.

---

## Answers to the required questions

### 1. Working archive URL template

**Confirmed as expected:**

```
https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{quarter}.zip
```

Verified by:
- Downloading `2024q1.zip` directly (HTTP 200, 124,336,804 bytes, valid zip archive).
- Scraping all `.zip` links from the current documentation page — every link
  follows this exact template, e.g. `/files/dera/data/financial-statement-data-sets/2024q1.zip`.
- Spot-checking boundary quarters with `HEAD` requests (see Q2).

No adjacent-quarter fallback was needed — `2024q1` worked on the first try.

### 2. Earliest and latest available quarter

- **Earliest: `2009q1`** — `HEAD` request returns HTTP 200. `2008q4` returns
  HTTP 404 (confirms `2009q1` is the true start, not just the first one linked).
- **Latest: `2026q1`** — `HEAD` request returns HTTP 200 and is the last
  quarter linked on the documentation page. `2026q2` returns HTTP 404 (not
  yet posted as of the verification date, 2026-08-18, despite Q2 2026 having
  ended June 30). Later tasks should not assume the most-recently-ended
  calendar quarter is always available — there is a posting lag, and a
  404 on the newest quarter is expected/normal, not an error condition.

### 3. Exact columns of each file

Confirmed via `head -1 <file>.txt | tr '\t' '\n' | nl` against the extracted
`2024q1` archive. All files are tab-delimited with a header row.

**`sub.txt`** (36 columns, 6,029 lines incl. header → 6,028 data rows):

```
1 adsh          10 baph          19 stprinc       28 fy
2 cik           11 mas1          20 ein           29 fp
3 name          12 mas2          21 former         30 filed
4 sic           13 countryma     22 changed        31 accepted
5 countryba     14 stprma        23 afs            32 prevrpt
6 stprba        15 cityma        24 wksi           33 detail
7 cityba        16 zipma         25 fye            34 instance
8 zipba         17 bas1          26 form            35 nciks
9 bas2          18 countryinc    27 period          36 aciks
```

**`num.txt`** (10 columns, 3,428,695 lines incl. header → 3,428,694 data rows):

```
1 adsh    5 qtrs    9 value
2 tag     6 uom    10 footnote
3 version 7 segments
4 ddate   8 coreg
```

**`tag.txt`** (9 columns, 86,530 lines incl. header):

```
1 tag  4 abstract  7 crdr
2 version 5 datatype 8 tlabel
3 custom  6 iord     9 doc
```

**`pre.txt`** (10 columns, 726,156 lines incl. header):

```
1 adsh    5 inpth    9 plabel
2 report  6 rfile   10 negating
3 line    7 tag
4 stmt    8 version
```

These column lists were cross-checked against `readme.htm` (bundled inside
the same archive) and match exactly, including field order.

### 4. Does `num.txt` contain a `segments` column?

**Yes — confirmed present at column position 7**, and it is meaningfully
populated, not a vestigial/always-empty field.

- Total data rows in `num.txt` (2024q1): 3,428,694
- Rows with a **non-empty** `segments` value: 1,888,203 (~55%)

The SEC's current documentation page states explicitly (as of a December
2024 reprocessing of the entire Financial Statement Data Sets archive):

> "The layout and fields remain the same apart from the NUM file where a
> new field 'segments' has been added."

The bundled `readme.htm` describes it as: *"Tags used to represent axis
and member reporting."* (ALPHANUMERIC, max length 1024.)

**Real non-empty examples pulled from `2024q1/num.txt`:**

```
adsh=0001628280-24-006850  tag=LongTermDebtNoncurrent
segments = DebtInstrument=A415NotesDue2043;

adsh=0001174947-24-000361  tag=PolicyholderBenefitsAndClaimsIncurredNet
segments = BusinessSegments=NonStandardAuto;

adsh=0001410578-24-000025  tag=PartnersCapital
segments = PartnerTypeOfPartnersCapitalAccount=GeneralPartner;TaxCreditSeries=SeriesTwentySeven;
```

Format is `Dimension=Member;` pairs, semicolon-delimited when a fact has
more than one XBRL dimension applied. `BusinessSegments=NonStandardAuto;`
is a direct example of a genuine reporting-segment dimension, confirming
segment-level financial analysis is possible using this field in Stage 2 —
but note it is XBRL axis/member text (arbitrary company-chosen dimension
names, e.g. `DebtInstrument=`, `PartnerTypeOfPartnersCapitalAccount=`, not
just `BusinessSegments=`), so isolating "true" business-segment breakdowns
from other dimensional facts (debt instruments, tax credits, investment
identifiers, etc.) will require filtering on dimension name, not just
presence/absence of the column.

**Caveat for downstream CSV/TSV parsing:** some `segments` values contain
embedded commas, embedded double quotes, and embedded newlines (e.g.
`InvestmentIdentifier=Abaco Energy Technologies LLC, Preferred Equity`,
and multi-line values with escaped `""` quoting). A naive line-based
tab-split parser will corrupt these rows; later ingestion tasks must use a
real quoted-field-aware parser (or confirm the field is never embedded with
raw tabs) rather than plain `str.split('\t')`.

### 5. Date/integer format confirmations

All confirmed exactly as assumed, checked against the full `2024q1` files:

| Field | Assumed format | Verified | Notes |
|---|---|---|---|
| `sub.txt.filed` | `YYYYMMDD` | ✅ | 8 chars on every one of 6,028 rows; 0 blank |
| `sub.txt.fye` | `MMDD` | ✅ | 4 chars on every non-blank row; **12 of 6,028 rows have `fye` blank** (filer did not declare one) |
| `num.txt.ddate` | `YYYYMMDD` | ✅ | 8 chars on every one of 3,428,694 rows |
| `num.txt.qtrs` | integer | ✅ | Every value matches `^-?[0-9]+$`, 0 blank. Observed range 0–120. Values above ~40 are likely filer data-quality anomalies, not a format problem — `qtrs` is always a plain integer string, never decimal. |

### 6. Stated rate limit

From `https://www.sec.gov/about/webmaster-frequently-asked-questions`
(redirect target of the brief's FAQ URL), verbatim:

> "Note that our current maximum access rate is 10 requests per second.
> This is carefully monitored to preserve equitable access for all users."

And the required User-Agent format, verbatim from the same page's sample
header block:

```
User-Agent: Sample Company Name AdminContact@<sample company domain>.com
Accept-Encoding: gzip, deflate
Host: www.sec.gov
```

This confirms the "`<Name> <email>`" User-Agent format specified in this
project's global constraints (`Robert McCourt rmmccourt01@comcast.net`) is
the correct shape, and the 10 requests/second limit matches exactly.

---

## Step 4: Contradictions check

**No assumption in the Global Constraints or the task brief was
contradicted.** Specifically:

- Archive URL template: matches exactly.
- Rate limit: matches exactly (10 req/sec).
- User-Agent format: matches exactly (`<Name> <email>`).
- All four files' column names: present and match what later tasks will
  presumably hard-code (see column lists above).
- Date/integer formats: all confirmed exactly as assumed.
- `segments` column: exists, as hoped, and is usable for segment-level
  analysis (this was the open question, not an assumption to contradict).

**Two non-blocking findings worth downstream awareness (not
contradictions, do not require stopping):**

1. The two documentation URLs named in the brief now 301-redirect to new
   SEC site paths. If any later task links to these doc pages directly
   (not the data archive itself), it should use the new canonical URLs.
2. `segments` values can contain embedded commas/quotes/newlines and must
   be parsed with a proper quoted-field-aware TSV/CSV parser, not naive
   `str.split('\t')`, or ingestion will silently corrupt a subset of rows.

## Verification artifacts

Raw responses, extracted archive, and inspection commands were run from a
scratch directory outside the repository and are not committed. Commands
used are reproduced in full in `.superpowers/sdd/2026-08-18-stage1-bitemporal-data-foundation/task-1-report.md`.
