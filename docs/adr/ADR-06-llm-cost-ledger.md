# ADR-06: A per-call ledger for LLM tokens and cost

- **Status:** Accepted
- **Date:** 2026-10-09
- **Ticket:** WTA-18

## Context

A SaaS on top of an LLM has to know what each job costs: that number sets the price. The
first idea was to add counters to the `job` table, but a job makes several calls:

- one call per page;
- one more call per page that needs the self-healing retry;
- calls that are billed and still produce nothing: invalid output, a refusal, a response cut
  off at the token limit.

A fallback can also serve a call with a different model, at a different price.

## Decision

**One row per billed call goes in `llm_calls`, and a job's totals are SUMs over its rows.**

- Each row records:
  - the job and the page;
  - the agent (`designer`);
  - the model that served the call, which is the one priced;
  - the attempt number and the outcome (`ok`, `invalid`, `refusal`, `max_tokens`);
  - the four token counts: input, output, cache reads, cache writes;
  - `cost_usd`.
- `cost_usd` is computed when the call happens, from the list price, with cache writes at
  1.25x and reads at 0.1x of the input price. A later price change does not rewrite history.
- Every call is logged before deciding whether the page failed, so failures are counted too.
- When a job is retried, its earlier calls keep counting toward its cost: they were paid for.
- The data is exposed in `GET /jobs/{id}/usage` and can be queried with SQL. It is not in
  the customer report.

## Consequences

- Cost questions are answered with SQL: per job, per page, per model, and money lost to
  retries. Measured: about $0.04 per page with Opus 5.5, about $0.33 per 8-page job, so 1,000
  jobs a month is about $330 of LLM spend. That is the floor for pricing.
- New agents (a Documenter that calls an LLM, for example) add rows with no schema change.
- Prices live in `MODEL_PROFILES` and must be kept current. An unknown model is stored with
  `cost_usd = NULL` and counted apart, never as free.
