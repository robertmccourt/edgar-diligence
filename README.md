# edgar-diligence

A point-in-time SEC fundamentals store and a cited financial analysis agent,
built to answer one question: **can you trust an AI financial analyst?**

## The problem

Most financial databases silently overwrite restated figures and give no
indication of when a number became publicly knowable. A model trained or
evaluated on that data is quietly seeing the future — the same failure mode
as training on a lab value corrected after the outcome. In quantitative
finance this is called look-ahead bias, and point-in-time databases that
avoid it are expensive commercial products.

This project builds a free one from SEC bulk data, then puts a citing agent
on top of it and measures how often that agent fabricates.

## What it does

- **Bitemporal fact store.** Every fact carries the period it describes *and*
  the date it was published. Restatements are appended, never overwritten.
  Queries answer "what was knowable as of date D".
- **Canonical schema + auditable mapping.** Thousands of heterogeneous XBRL
  tags projected onto 10 canonical fields, with every mapping decision logged
  and reproducible.
- **Cited analysis agent.** Produces a company memo where every numeric claim
  resolves to a fact ID that resolves to an SEC accession number — and which
  cannot see anything filed after the as-of date.
- **Groundedness evaluation.** Claims are typed and verified individually,
  with a human-calibrated judge and a temporal leakage metric.

## Status

Stage 1 (data foundation) in progress. See `docs/superpowers/`.

## Documentation

| Document | Contents |
|---|---|
| `docs/superpowers/specs/` | Design specification |
| `docs/superpowers/plans/` | Implementation plans |
| `docs/verification/` | Verified external assumptions (SEC formats, limits) |
| `docs/data-dictionary.md` | Generated from the live schema |

## Data source

SEC EDGAR Financial Statement Data Sets (DERA), public domain. All requests
send a declared `User-Agent` and are rate-limited per SEC guidance.
