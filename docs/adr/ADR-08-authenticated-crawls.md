# ADR-08: Authenticated crawls with one-job credentials

- **Status:** Accepted
- **Date:** 2026-10-17
- **Ticket:** WTA-29

## Context

For many sites the real product is behind a login: a dashboard, an account area, an admin.
Without signing in, the explorer only sees the login page and the Designer only writes
scenarios for it (ADR-01 listed this as an accepted limitation). Customers will want the
pages behind the login covered, which means they have to hand us a username and a password.

That makes us custodians of a secret that opens someone else's system. The design has to
answer: where the secret lives, for how long, who can read it, what the crawler is allowed
to do while it acts as that user, and what we ask of the customer.

## Decision

**Credentials are given per job, encrypted at rest, used only by the browser, and deleted
the moment the crawl ends.**

- **Per job, never per account.** `POST /jobs` takes an optional `login` object (login
  page, username, password, an explicit `authorized: true`). Nothing is kept "for next
  time": there is no vault of customers' passwords to protect, only a secret that lives for
  the minutes a crawl takes.
- **Encrypted with a server key** (`CREDENTIALS_KEY`, Fernet) before the row is written,
  because the API and the worker share state only through the database (ADR-03). The
  database holds an opaque blob; a dump of it is useless without the key. A server without
  the key refuses jobs with a login (503) and keeps working for everything else.
- **Decrypted only inside the explorer node**, handed to the browser as a `LoginSpec`
  whose `repr` hides the password, and **cleared from the row in a `finally`** right after
  the crawl, whatever happened. `mark_job_done`, `mark_job_failed` and a worker start-up
  sweep (finished jobs, or older than an hour) clear it again as safety nets.
- **Never anywhere else.** Not in the graph state (which is logged and traced), not in the
  API responses (`JobRead` shows `login_url` only), not in the report, and **never in the
  LLM prompt**: the Designer gets page inventories, and whatever enters a prompt ends up in
  traces and on the provider's side.
- **The login itself is deterministic** (ADR-01 still holds): find the visible password
  field, the text/email field before it, the form's submit; fill, submit, and succeed only
  if no password field is visible afterwards. A failed login does not fail the job: the
  crawl continues anonymously and the job's `error` says so.
- **Guardrails while logged in.** The crawler now acts as a real user, so it never follows
  links whose path contains an action word (`logout`, `delete`, `checkout`, `pay`,
  `cancel`, ...; `UNSAFE_LINK_WORDS`). It still only issues GETs, stays on the domain,
  honours the page limit and the anti-SSRF check (the login page included; it must be on
  the site under test).
- **What we ask of the customer**, in the form and in the API docs: a **test account** on a
  **test environment**, never an admin or a real customer's account, and an explicit
  confirmation that they are authorized to test that site with that account.

## Consequences

- Sites behind a login are covered, which is most SaaS products. Pages reached only through
  multi-step interactions are still out of scope.
- WTA-35 addendum: the login URL no longer has to be the form itself (home page with a
  "Log in" link, private page that redirects, two-step forms, sibling auth subdomain all
  work), cart/checkout pages are crawled (view-only GETs), and the Executor gets a `login`
  tool plus logged-out login-page scenarios. Still deterministic, still no LLM in the login.
- No 2FA, captchas or social login: customers with those must provide a test account
  without them. Deliberate: solving them means fighting the site's own protections.
- Rotating `CREDENTIALS_KEY` only affects jobs that are mid-crawl at that moment (they
  continue anonymously and say why); nothing long-lived depends on the key.
- The heuristic login will miss unusual forms (username and password on separate steps, or
  the password field rendered after a click). Each such case is a unit test on the local
  site first, then a rule, like the page classifier (ADR-04).
- Keeping credentials per account (a vault) would be a new decision with its own ADR, and
  the answer should still be "ask the customer for a dedicated test user" for as long as
  possible.
