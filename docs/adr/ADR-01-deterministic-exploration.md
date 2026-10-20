# ADR-01: Deterministic exploration, no LLM in the crawl

- **Status:** Accepted
- **Date:** 2026-10-04
- **Ticket:** WTA-10 (crawler), WTA-11 (element extraction)

## Context

To design test scenarios we first need to know what a site contains: its pages, its forms,
its buttons. There are two ways to explore it:

1. An **LLM agent** that drives a browser with tools (open, click, read the DOM, decide
   where to go next).
2. A **deterministic crawler**: breadth-first over same-domain links, with fixed limits.

The agent is the fashionable option and can reach pages behind interactions. But it makes
dozens of calls per job, each carrying DOM or screenshots in its context. Its path changes
between runs, and it is hard to bound in time and money. At SaaS scale, the margin of a job
would go to exploration before any scenario is written.

## Decision

**The exploration is deterministic and spends zero LLM tokens.**

- A same-domain BFS with `max_pages=8` and a 15-second timeout per page. Closer pages come
  first, because they are the most important ones.
- Playwright renders each page, so JavaScript sites work.
- One `page.evaluate` extracts a compact JSON inventory (forms, fields, buttons, navigation)
  of under 2 KB. Raw HTML is never extracted.
- Guardrails:
  - robots.txt is honored;
  - the crawler identifies itself with a User-Agent;
  - every URL passes the anti-SSRF check before it is visited, links discovered during the
    crawl included.

The intelligence goes where it adds value: the Designer receives the inventory, plus the
description the user gave of the app.

## Consequences

- Exploring costs the same on every run, and the same site gives the same pages. That is
  reproducible, and it can be tested with a local site.
- The user can also explore first and choose the pages (WTA-32): a wider crawl costs time,
  not tokens, and the LLM is only spent on the pages they keep.
- Pages reachable only through interactions are missed (multi-step wizards). Content behind
  a login is covered since ADR-08, with a deterministic sign-in and per-job credentials.
- An LLM-guided exploration remains possible later as a premium mode. It would reuse the
  same guardrails: the page limit, the domain lock, the timeouts and the anti-SSRF check.
