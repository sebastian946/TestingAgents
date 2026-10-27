# Deployment

How to run Testing Agents on a server of your own, with HTTPS, in about an hour. The
production setup is the development `docker compose` plus one overlay file: the same
images, the same `.env` keys, nothing to rewrite.

## Why a single server

The worker writes each Excel report to the `reports_data` volume and the API serves the file
from that same volume, so the two containers have to share a disk. Platforms that run every
service on its own machine (Render, Railway, Fly) need the reports moved to object storage
first (S3 or similar), which is a code change. A VPS runs exactly what you already run
locally, and the worker's Chromium gets the memory it needs.

| Option | Cost | Notes |
|---|---|---|
| **VPS with 4 GB RAM** (Hetzner, DigitalOcean, Lightsail) | US$5–24 / month | **This guide.** Same compose, no code changes |
| PaaS + managed Postgres and Redis | US$20–50 / month | Move the reports to S3 first |
| Kubernetes, ECS | more | Not for an MVP |

2 GB of RAM is the minimum (Chromium); 4 GB is comfortable.

## What production changes

`app/docker-compose.prod.yml` is applied on top of `app/docker-compose.yml`:

| Change | Why |
|---|---|
| Postgres, Redis and the API publish **no ports** on the host | A published port bypasses `ufw`; only Caddy listens on 80/443 |
| Redis requires `REDIS_PASSWORD` | Anyone who can reach Redis can enqueue jobs |
| **Caddy** in front: HTTPS with an automatic Let's Encrypt certificate for `DOMAIN`, the dashboard built into its image (`frontend/Dockerfile`), the API proxied under the same origin | No certificate chores, no CORS (one origin); HSTS and security headers in `app/deploy/Caddyfile` |
| `SQL_ECHO=false` | SQL logs (LLM text included) would fill the disk |

Authentication (`POST /auth/register`, `/auth/login`) is part of the API itself since WTA-26:
every `/jobs` route needs a token, jobs belong to the account that created them, and each
account may create `AUTH_MAX_JOBS_PER_HOUR` jobs (default 20). A leaked URL alone cannot run
up the Anthropic bill.

## Step by step

### 1. The server

Ubuntu 24.04, 4 GB RAM, SSH with a key. Then:

```bash
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
```

### 2. DNS

An `A` record for your hostname (e.g. `api.example.com`) pointing at the server's IP. Caddy
needs it to obtain the certificate, so do this before starting the stack.

### 3. Code and configuration

```bash
git clone https://github.com/sebastian946/TestingAgents.git /opt/testing-agents
cd /opt/testing-agents
cp .env.example .env
chmod 600 .env
nano .env
```

Set in `.env`:

| Key | Value |
|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | new, long values (not `myuser` / `password`); they are fixed when the Postgres volume is first created |
| `ANTHROPIC_API_KEY` | a key **for production only**, with a monthly spend limit set in the Anthropic console |
| `AUTH_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `REDIS_PASSWORD` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DOMAIN` | the hostname from step 2 |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT=production` | error reports with the stack trace, by email, from sentry.io (free plan). Create a project of type FastAPI and copy its DSN |
| `LEMONSQUEEZY_WEBHOOK_SECRET`, `LEMONSQUEEZY_VARIANT_STARTER`, `LEMONSQUEEZY_VARIANT_PRO`, `LEMONSQUEEZY_CHECKOUT_STARTER_URL`, `LEMONSQUEEZY_CHECKOUT_PRO_URL` | billing (ADR-09). In Lemon Squeezy: create a store, two subscription products (Starter US$19/month, Pro US$49/month), copy each variant id and its "Share" checkout URL, and add a webhook to `https://DOMAIN/billing/webhook/lemonsqueezy` with the subscription events and a signing secret. Without these, everyone stays on `DEFAULT_PLAN` and the dashboard says payments are not enabled |
| `DEFAULT_PLAN` | `free` in production |
| `RETENTION_DAYS` | `90`: finished jobs older than that are deleted with their files |
| `CREDENTIALS_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`: encrypts the login credentials customers give for a job (deleted after the crawl). Without it, jobs with a login are refused |
| `CORS_ORIGINS` | not needed when the dashboard is served by Caddy (same origin). Only for a frontend hosted elsewhere: its exact origin, e.g. `["https://app.example.com"]` |
| `EMAIL_BACKEND=resend`, `RESEND_API_KEY`, `EMAIL_FROM` | verification and reset emails. Create a free account at resend.com, add your domain (two DNS records they show you), create an API key, and use a sender on that domain, e.g. `Testing Agents <no-reply@example.com>`. With `console` nobody receives the links |
| `APP_URL` | `https://app.example.com`: the links in emails point here |
| `LANGSMITH_*` | optional; tracing a production job stores its pages and scenarios in LangSmith |

`.env` is git-ignored: it never leaves the server except in your own backups.

### 4. Start

```bash
docker compose --env-file .env -f app/docker-compose.yml -f app/docker-compose.prod.yml up -d --build
docker compose --env-file .env -f app/docker-compose.yml -f app/docker-compose.prod.yml ps
curl https://api.example.com/Health
```

The first build takes a few minutes (Chromium for the worker, `npm ci` for the dashboard).
`ps` should show every service `healthy`; Caddy's log (`docker compose ... logs caddy`) shows
the certificate being issued. Open `https://DOMAIN/` for the landing, `https://DOMAIN/dashboard` for the app, `https://DOMAIN/docs`
for the API.

### 4b. Lemon Squeezy setup

Billing follows ADR-09. Everything below is done once in the Lemon Squeezy dashboard,
first with **Test mode** on (toggle in the left sidebar), then again in live mode.

1. **Store**: Settings > Stores. Name `Tests Wise`, URL `https://app.tests-wise.com`,
   currency USD. Settings > Payouts: the bank account that receives the money.
2. **Products**: Products > New product, twice: `Tests Wise Starter` (subscription,
   US$19 per month) and `Tests Wise Pro` (subscription, US$49 per month). Each product has
   one variant; copy its id from the variant's URL or the "..." menu into
   `LEMONSQUEEZY_VARIANT_STARTER` / `LEMONSQUEEZY_VARIANT_PRO`.
3. **Checkout links**: on each product, Share > copy the checkout URL
   (`https://<store>.lemonsqueezy.com/buy/<uuid>`) into
   `LEMONSQUEEZY_CHECKOUT_STARTER_URL` / `LEMONSQUEEZY_CHECKOUT_PRO_URL`. The dashboard
   appends the user's email and id (`checkout[custom][user_id]`) so the webhook knows who
   bought.
4. **Webhook**: Settings > Webhooks > Add endpoint. URL
   `https://app.tests-wise.com/billing/webhook/lemonsqueezy`, a long random signing
   secret of at most 40 characters (`python3 -c "import secrets; print(secrets.token_hex(20))"`) into
   `LEMONSQUEEZY_WEBHOOK_SECRET`, events `subscription_created`, `subscription_updated`,
   `subscription_cancelled`, `subscription_resumed`, `subscription_expired`,
   `subscription_paused`, `subscription_unpaused`, `subscription_payment_failed`,
   `subscription_payment_recovered`.
5. **API key and store id** (recommended): Settings > API > **+**, name `tests-wise-api`,
   copy the key into `LEMONSQUEEZY_API_KEY`; Settings > Stores shows the store `#number`,
   copy it into `LEMONSQUEEZY_STORE_ID`. Together they let users cancel or resume from
   our Plan page (`POST /billing/cancel` / `/resume`) and make every checkout a per-user
   one created through the Checkouts API (`POST /billing/checkout`: email prefilled, our
   user id attached, brand color, "Back to Tests Wise" on the receipt and an automatic
   redirect to `/billing?checkout=success`). Without them the share links of step 3 are
   used as they are.
6. **Pro free trial**: in the Pro product's variant, turn on "Subscription has free
   trial" with 7 days (`Plan.trial_days` in `config/plans.py` must match what the
   landing promises). The trial needs a card; the webhook reports `on_trial`, which
   keeps the plan.
7. **Customer portal and emails**: Settings > General: enable the customer portal
   (cancellation, card update) and the receipt emails; Design: logo and brand color
   `#b8431a`.
8. Restart the API (`docker compose ... up -d api`) after editing `.env`, open
   `/billing`, pick Starter and pay with the test card `4242 4242 4242 4242` (any future
   date, any CVC). The dashboard must show the Starter plan within seconds and
   Settings > Webhooks must list the delivery as 200. "Manage subscription" must open
   the portal. Live mode: repeat steps 2 to 4 (variant ids, links and the webhook are
   per mode) and complete the store verification Lemon Squeezy asks for before the
   first payout.

### 4c. Go live: domain, search engines, link previews

- **Root domain**: `app.tests-wise.com` is the site; make `tests-wise.com` and `www` reach it.
  In Squarespace Domains > the domain > Forwarding: forward `tests-wise.com` to
  `https://app.tests-wise.com`, permanent (301), "forward path" on. No server change.
- **Search engines**: the landing ships `robots.txt` (private SPA routes disallowed),
  `sitemap.xml`, canonical links, Open Graph / Twitter tags with `og-image.png` and a
  JSON-LD `SoftwareApplication` block (`frontend/public/`). The SPA's `index.html` is
  `noindex`. After deploying: Google Search Console > Add property > Domain
  `tests-wise.com` > verify with the DNS TXT record it shows (Squarespace DNS) > Sitemaps >
  submit `https://app.tests-wise.com/sitemap.xml`; Bing Webmaster Tools can import the
  Search Console property. Indexing takes days; search `site:app.tests-wise.com` to check.
- **Link previews**: paste the URL in https://www.opengraph.xyz or LinkedIn's Post
  Inspector (https://www.linkedin.com/post-inspector/) once deployed; the image is
  `frontend/public/og-image.png` (1200x630, regenerate with the scratch script if the
  copy changes).

### 5. Smoke test

`app/deploy/smoke.sh` checks a running deployment without changing anything: it waits for
`GET /Health/ready` (database reachable and migrated to the latest revision, Redis, at least
one worker on the queue), then the landing, the dashboard shell, the legal pages,
`robots.txt`/`sitemap.xml`, that the API demands a login, that the billing webhook rejects a
bad signature, the HSTS and CSP headers and how long the TLS certificate is still valid.

```bash
app/deploy/smoke.sh                              # on the server (https://$DOMAIN from .env)
bash app/deploy/smoke.sh https://app.tests-wise.com   # from anywhere
```

It prints one `ok`/`FAIL` line per check and exits 1 on any failure. `/Health` stays the
cheap liveness check Docker uses; `/Health/ready` answers 503 with the failing check
(`database: down|behind`, `redis: down`, `worker: none|down`) and never an error message.

### 6. Updating: CI/CD (WTA-38)

Every pull request runs `.github/workflows/ci-cd.yml`: backend unit tests, `alembic check`
against a real PostgreSQL, the integration test, frontend lint and build, shellcheck, the
compose files and `caddy validate`. A push to `main` (a merged PR) runs the same checks and
then deploys:

1. GitHub connects with a deploy key whose only allowed command is
   `app/deploy/ssh-entry.sh` (`restrict` in `authorized_keys`: no shell, no forwarding). It
   accepts `deploy <sha>` and `smoke`, nothing else.
2. `deploy.sh` of that commit takes a lock, dumps the database to
   `/var/backups/testing-agents/predeploy-*.sql.gz` (keeps 10), checks out the commit,
   runs `docker compose ... up -d --build` and the smoke test.
3. If the build or the smoke test fails, it checks out the previous commit, rebuilds and
   smoke-tests again, and the workflow turns red. A migration is not undone: the dump of
   step 2 is the way back (`gunzip -c <dump> | docker exec -i postgres_db psql -U $DB_USER $DB_NAME`).
4. The workflow runs the smoke test once more from GitHub, through DNS and TLS.

`.github/workflows/uptime.yml` runs the smoke test every 30 minutes; a red run means
production needs attention, and GitHub emails it.

One-time setup:

```bash
# On the server, once, after merging the CI/CD pull request
cd /opt/testing-agents && git pull
docker compose --env-file .env -f app/docker-compose.yml -f app/docker-compose.prod.yml up -d --build
ssh-keygen -t ed25519 -N "" -C github-deploy -f /root/.ssh/github_deploy
echo "restrict,command=\"/bin/bash /opt/testing-agents/app/deploy/ssh-entry.sh\" $(cat /root/.ssh/github_deploy.pub)" >> /root/.ssh/authorized_keys
cat /root/.ssh/github_deploy          # the private key: paste it into the DEPLOY_SSH_KEY secret
ssh-keyscan -t ed25519 SERVER_IP 2>/dev/null   # the line it prints is DEPLOY_KNOWN_HOSTS
rm /root/.ssh/github_deploy           # GitHub keeps the only copy of the private key
```

In GitHub (Settings > Environments > `production` > secrets, or with `gh`):

| Name | Kind | Value |
|---|---|---|
| `DEPLOY_HOST` | environment secret | the server IP |
| `DEPLOY_SSH_KEY` | environment secret | the private key printed above, whole, with its BEGIN/END lines |
| `DEPLOY_KNOWN_HOSTS` | environment secret | the `ssh-keyscan` line above |
| `APP_URL` | repository variable | `https://app.tests-wise.com` |

Then Actions > CI/CD > Run workflow (branch `main`) deploys the current commit; from then on
merging a pull request is the whole release. Optional: Settings > Branches > protect `main`
and require the four CI checks, and add yourself as required reviewer of the `production`
environment to approve each deploy by hand.

By hand (same script, same rollback): `ssh root@SERVER` then
`/opt/testing-agents/app/deploy/deploy.sh $(git -C /opt/testing-agents rev-parse origin/main)`
after a `git fetch`.

Restarting the worker matters: a running worker keeps the code it started with; `up -d
--build` recreates it. A job that was running is marked `failed` by RQ and retried.
Migrations run in the api container (`alembic upgrade head` before uvicorn).

### 7. Backups

`app/deploy/backup.sh` dumps the database, compressed, and keeps 14 days:

```bash
mkdir -p /var/backups/testing-agents
crontab -e
# 0 3 * * * /opt/testing-agents/app/deploy/backup.sh >> /var/log/testing-agents-backup.log 2>&1
```

Copy the dumps off the server (S3, Backblaze, `rsync` to another machine). The Excel files in
`reports_data` are not backed up on purpose: the API rebuilds a missing report from the
database.

Restore: `gunzip -c db-2026-10-16-0300.sql.gz | docker exec -i postgres_db psql -U $DB_USER -d $DB_NAME`.

### 8. Monitoring

- Uptime: the `Uptime` workflow (smoke test every 30 minutes, see step 6). An external
  monitor (UptimeRobot, Better Stack) on `https://DOMAIN/Health/ready` adds alerts by SMS.
- Jobs and cost: LangSmith shows every job as a trace; `GET /jobs/{id}/usage` and the
  `llm_calls` table (ADR-06) answer cost questions with SQL.
- Logs: `docker compose ... logs -f worker` (or `api`, `caddy`). Docker keeps them on disk;
  add `logging: {options: {max-size: "50m", max-file: "3"}}` to the services if they grow.

## Security posture

What the deployment gives you, so you can answer customers' questions:

- Transport: HTTPS only (Caddy, Let's Encrypt, HSTS), HTTP redirects. Security headers
  including a strict `Content-Security-Policy` for the dashboard.
- Network: only Caddy is reachable from the internet; Postgres, Redis and the API talk over
  the compose network. Redis requires a password.
- Processes: the API and the worker run as an unprivileged user with every Linux capability
  dropped and `no-new-privileges`. The worker's Chromium, which visits third-party sites, has
  no path to the host.
- Accounts: argon2id password hashes, leaked-password check, rate limits on login and reset,
  email verification before spending, JWTs that die on password reset.
- Secrets customers give us: per-job login credentials encrypted with Fernet, deleted right
  after the crawl, never logged or sent to the LLM (ADR-08).
- Data: jobs belong to one account, deletable by its owner together with the account;
  finished jobs expire after `RETENTION_DAYS`; nightly encrypted backups you keep off-site.
- Payments: never touch our servers (Merchant of Record, ADR-09).
- Dependencies: Dependabot opens PRs for vulnerable packages; Sentry reports runtime errors.

## Scaling later

- More throughput on the same server: `docker compose ... up -d --scale worker=3`. Workers
  share the queue; each needs about 1 GB of RAM while Chromium runs.
- Several servers: move the reports to object storage, then managed Postgres and Redis, then
  a PaaS or container service. Nothing in the API depends on the machine except that volume.

## Checklist before going live

- [ ] `AUTH_SECRET_KEY`, `REDIS_PASSWORD`, `DB_PASSWORD` are long random values (the API logs
      a warning at start when the auth key is short or the placeholder).
- [ ] `ANTHROPIC_API_KEY` has a spend limit.
- [ ] `CREDENTIALS_KEY` set, if customers will test sites behind a login.
- [ ] `SENTRY_DSN` set and a test error visible in Sentry.
- [ ] Lemon Squeezy: webhook created and a test purchase (test mode) upgraded an account; `DEFAULT_PLAN=free`.
- [ ] A backup dump restored on a clean database at least once.
- [ ] Terms of service and privacy policy linked from the registration page.
- [ ] Dependabot enabled in the repository settings (`.github/dependabot.yml` is in place).
- [ ] `EMAIL_BACKEND=resend` with the domain verified: register a test account and click the link you receive.
- [ ] `ufw` is on, and `docker compose ... ps` shows only Caddy with published ports.
- [ ] `https://DOMAIN/Health` answers over HTTPS; `http://` redirects.
- [ ] The backup cron ran at least once and the dump is readable.
