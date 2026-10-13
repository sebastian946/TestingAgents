# ADR-03: Asynchronous jobs, with 202 + polling and an RQ worker

- **Status:** Accepted
- **Date:** 2026-10-01
- **Tickets:** WTA-3 (API), WTA-6/7 (queue and worker), WTA-14 (live progress)

## Context

A job crawls a site with a real browser, makes one LLM call per page and writes an Excel
file. It takes from 20 seconds to several minutes. An HTTP request that waits that long
times out in proxies and browsers. A dropped connection also loses the work, and the API
process is tied up while the job runs.

## Decision

**`POST /jobs` only stores the job and enqueues its id. It answers `202 Accepted` at once.
A separate worker does the work, and the client polls `GET /jobs/{id}` until the job is
`done` or `failed`.**

- **The queue is RQ on Redis.** RQ is a small library: a task is a plain Python function,
  and Redis is needed anyway. Celery would bring brokers, result backends and configuration
  this MVP does not need. FastAPI's `BackgroundTasks` runs the work inside the API process,
  so a restart loses it and heavy jobs compete with requests.
- **Only the job id travels through Redis.** The API and the worker share state only through
  PostgreSQL, never in memory. Either can restart without losing anything that was committed.
- **Progress is committed page by page.** Each page, together with its counter increment, is
  committed in one short transaction. `GET /jobs/{id}` shows `pages_crawled` and
  `total_scenarios` growing while the job runs, and a crash keeps the work already done.
- The report endpoint answers `409` with `Retry-After` while the job runs. It answers `404`
  only when the job does not exist, so a polling client never takes "not ready" for
  "wrong id".

## Consequences

- The API stays fast and stateless, and it scales separately from the worker. Adding
  workers is enough to process more jobs.
- Clients have to poll (or, later, receive a webhook). The integration test shows the right
  way: a poll with a timeout, never a fixed sleep.
- A worker runs the code it started with: after a deploy it has to be restarted. Two workers
  on one queue split the jobs between them, so the integration test uses its own queue name.
