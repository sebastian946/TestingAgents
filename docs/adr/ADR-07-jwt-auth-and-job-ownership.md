# ADR-07: Email + password accounts, JWT tokens, jobs owned per user

- **Status:** Accepted
- **Date:** 2026-10-16
- **Ticket:** WTA-26

## Context

Until now the API was open: anyone who reached it could create jobs and read every job.
That is fine on a laptop and unacceptable on a server. Each job spends real money (ADR-06:
about US$0.33 for 8 pages) and makes our server crawl third-party sites. We need to know
who asked for a job, show each customer only their own work, and bound what one account
can spend.

Options considered:

1. **A shared API key** in a header. One line of code, but one secret for everybody: no
   per-customer view, no way to revoke one customer, and it does not work for a browser
   frontend that different people log in to.
2. **Email + password with server-side sessions** (a session table or Redis). Standard, and
   sessions can be revoked at once. But it adds a lookup on every request and state the
   API has to share, when we want API instances to stay interchangeable (ADR-03).
3. **Email + password with signed JWT access tokens.** Stateless: the token carries the
   user id and an expiry, and the API verifies the signature. Revocation is coarse
   (rotate the key, everyone logs out) unless a denylist is added later.
4. **OAuth login (Google, GitHub).** Convenient, but needs public callback URLs and
   provider credentials before the product even has a domain.

## Decision

**Option 3.** Accounts are `users` rows (email unique and lower-cased, argon2id hash via
`pwdlib`). `POST /auth/register` and `POST /auth/login` return an HS256 JWT signed with
`AUTH_SECRET_KEY`, with `sub` = user id and a one-day expiry (`AUTH_TOKEN_EXPIRE_MINUTES`).
Both answer with the same token shape, so a client that just signed up is logged in.

- **Every `/jobs` route requires a token.** The router carries the dependency, so a new
  route cannot be added unprotected by accident. A missing, bad or expired token is a `401`
  with `WWW-Authenticate: Bearer`; `/Health` stays public.
- **A job belongs to the user who created it** (`job.user_id`). Routes load jobs through one
  path dependency that filters by owner, so another user's job is a `404`: indistinguishable
  from a job that does not exist, which leaks nothing.
- **An hourly job limit per account** (`AUTH_MAX_JOBS_PER_HOUR`, default 20, `429` past it).
  It is a plain count on `job.created_at`, no extra storage, and it caps what a leaked token
  or a client loop can cost.
- **Login never says which field was wrong**, and the password check runs even when the
  email is unknown, so neither the message nor the timing reveals which emails exist.
- No email verification and no password reset yet. Both need an email provider, and
  neither blocks the first customers; they are on the roadmap.

## Consequences

- The API stays stateless: scaling it is still "run another container" (ADR-03).
- Rotating `AUTH_SECRET_KEY` logs every user out at once; there is no per-token revocation.
  If that is ever needed, a `token_version` column on `users` checked by `get_current_user`
  gives per-user revocation with one cheap lookup.
- Jobs created before this change have `user_id = NULL` and are invisible through the API;
  existing databases need one `ALTER TABLE` (documented in the guide, until Alembic, WTA-4).
- The frontend keeps the token in the browser and sends it as a bearer header. CORS must
  then name the frontend's origin (`CORS_ORIGINS`) instead of `*`.
