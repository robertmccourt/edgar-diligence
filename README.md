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

Stage 1 (data foundation) and Stage 2 (cited agent + eval harness) built
and merged. Real-model runs and judge calibration pending. See
`docs/superpowers/`.

## Running the agent

Two provider configs, selected with `CONFIG=` (memos are stamped with the
config version that produced them, so runs never get conflated):

```sh
# Paid: Anthropic (claude-opus-5 writes, claude-sonnet-5 judges).
# Needs ANTHROPIC_API_KEY in .env.
make memo CIK=320193 AS_OF=2024-03-01

# Free: OpenRouter free-tier models. Needs OPENROUTER_API_KEY in .env
# (keys are free at openrouter.ai/keys).
make memo CIK=320193 AS_OF=2024-03-01 CONFIG=free
make eval MEMO=data/memos/<stem>.json CONFIG=free
```

OpenRouter's free tier allows ~50 requests/day (1,000/day after a one-time
$10 credit purchase) at up to 20 requests/minute. A full 11-section memo
can exceed the 50/day cap; single questions
(`python -m edgar.agent.run ... --question "..."`) use ~5-10 requests.

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
