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
the certificate being issued. Open `https://DOMAIN/` for the dashboard, `https://DOMAIN/docs`
for the API.

### 5. Smoke test

```bash
curl -X POST https://api.example.com/auth/register -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "a-long-password"}'
# -> {"access_token": "...", ...}
TOKEN=...
curl -X POST https://api.example.com/jobs -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://www.saucedemo.com/", "language": "en"}'
```

Then `GET /jobs/{id}` with the same header until `status` is `done`, and `GET /jobs/{id}/report`.

### 6. Updating

```bash
cd /opt/testing-agents && git pull
docker compose --env-file .env -f app/docker-compose.yml -f app/docker-compose.prod.yml up -d --build
```

This rebuilds and restarts the API and the worker. Restarting the worker matters: a running
worker keeps the code it started with. A job that was running is marked `failed` by RQ;
the client can submit it again.

Schema changes are applied by the api container itself: it runs `alembic upgrade head`
before serving, so a release that adds a migration needs nothing manual. To see the state:
`docker compose ... exec api alembic current`.

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

- Uptime: a free monitor (UptimeRobot, Better Stack) on `https://api.example.com/Health`.
- Jobs and cost: LangSmith shows every job as a trace; `GET /jobs/{id}/usage` and the
  `llm_calls` table (ADR-06) answer cost questions with SQL.
- Logs: `docker compose ... logs -f worker` (or `api`, `caddy`). Docker keeps them on disk;
  add `logging: {options: {max-size: "50m", max-file: "3"}}` to the services if they grow.

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
- [ ] `EMAIL_BACKEND=resend` with the domain verified: register a test account and click the link you receive.
- [ ] `ufw` is on, and `docker compose ... ps` shows only Caddy with published ports.
- [ ] `https://DOMAIN/Health` answers over HTTPS; `http://` redirects.
- [ ] The backup cron ran at least once and the dump is readable.
