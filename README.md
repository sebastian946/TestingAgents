# Testing Agents

Agent-assisted web testing SaaS. It takes a site's URL, crawls it, generates test scenarios
with an LLM and delivers a report. This repository contains **Step 1 (MVP Engine)**: API,
database, queue and worker.

The work plan lives in Notion:
[Tablero · SaaS Web Testing Agents — Paso 1 (Motor MVP)](https://app.notion.com/p/7b50aed7e8c34d909f606ed3c49f1642).

## Current status

| Block | Task | Status |
|---|---|---|
| A — Skeleton | Repository and folder structure | Done |
| A — Skeleton | Postgres and Redis with docker-compose | Done |
| A — Skeleton | FastAPI API: `POST /jobs` and `GET /jobs/{id}` | Done |
| A — Skeleton | SQLAlchemy models + CRUD | Done (Alembic pending) |
| A — Skeleton | Anti-SSRF URL validation | Done |
| B — Queue and Worker | Enqueue jobs with RQ, worker | Done |
| B — Queue and Worker | Failure handling (failed state, retry) | Pending |
| C — Explorer | Worker Dockerfile with Playwright + Chromium | Done |
| C — Explorer | Same-domain BFS crawler with page limit and robots.txt (WTA-10) | Done |
| C — Explorer | Element extraction per page: forms, buttons, navigation (WTA-11) | Done |
| C — Explorer | Screenshots, page_type (WTA-12, 13) | Pending |
| D to F | Designer, Documenter, Quality | Pending |

## Architecture

```
client ──POST /jobs──▶ API (FastAPI) ──▶ PostgreSQL  ◀── worker (RQ)  [pending]
            ◀──202────┘                       ▲              ▲
client ──GET /jobs/{id}──▶ API ───────────────┘              │
                                                Redis (queue) ┘  [pending]
```

Async job pattern: `POST /jobs` only stores the job in `queued` state and responds `202`.
The real work (crawl, LLM, report) will be done by the worker in a separate process; the
client polls progress with `GET /jobs/{id}`. API and worker share state **through the
database**, never in memory.

### Data model

```
job (uuid) ──1:N──▶ pages (serial) ──1:N──▶ scenarios (serial)
```

| Table | Key fields |
|---|---|
| `job` | `id` UUID, `url`, `status` (`queued` / `running` / `done` / `failed`), `pages_crawled`, `total_scenarios`, `report_path`, `error`, `created_at`, `started_at`, `finished_at` |
| `pages` | `job_id`, `url`, `title`, `page_type`, `screenshot_path`, `elements` (JSONB `PageInfo` inventory: forms with fields, buttons, nav links, headings) |
| `scenarios` | `job_id`, `page_id`, `scenario_code` (`SC-001`…, unique per job), `title`, `steps` (JSONB), `expected_result`, `priority`, `category` |

Deleting a job cascades to its pages and scenarios.

## Repository structure

```
TestingAgents/
├── .env                  # local variables (NOT versioned)
├── .env.example          # variables template
├── pyrightconfig.json    # points Pylance to the app/ venv
├── .vscode/settings.json # VS Code interpreter
├── FE/                   # frontend (empty for now)
└── app/                  # Python backend (uv project)
    ├── pyproject.toml / uv.lock
    ├── docker-compose.yml   # Postgres 16 + Redis 7 + api + worker
    ├── Dockerfile           # targets `api` and `worker` (worker adds Chromium)
    ├── main.py              # FastAPI app, CORS, /Health
    ├── config/variables.py  # Settings (pydantic-settings) read from .env
    ├── db/
    │   ├── conn.py          # engine, session, Base, get_db dependency
    │   ├── models_db/models_db.py  # Job, Page, Scenario tables
    │   ├── db_crud.py       # database operations
    │   └── init_db.py       # creates the tables (development shortcut)
    ├── models/models.py     # Pydantic input/output schemas
    ├── routes/routes.py     # /jobs endpoints
    ├── security/ssrf.py     # anti-SSRF validation of the job URL
    ├── db/redis/redis_conn.py  # Redis client, RQ queue, enqueue helper
    ├── worker/
    │   ├── worker.py        # starts the RQ worker
    │   ├── tasks.py         # what runs for each job (queued → running → done/failed)
    │   └── check_browser.py # Playwright smoke test
    ├── agents/  tests/      # pending
    └── .venv/               # created by uv (NOT versioned)
```

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [uv](https://docs.astral.sh/uv/) (manages Python and dependencies; installs Python 3.12 by itself)
- Optional: [DBeaver](https://dbeaver.io/) to browse the database

## Getting started

### 1. Environment variables

```powershell
copy .env.example .env
```

| Variable | Description | Default |
|---|---|---|
| `DB_USER` | PostgreSQL user | `myuser` |
| `DB_PASSWORD` | PostgreSQL password | `password` |
| `DB_NAME` | database | `local` |
| `DB_PORT` | port exposed on your machine | `5433` |
| `ENDPOINT` | PostgreSQL host as seen from the API | `localhost` |
| `REDIS_PORT` | exposed Redis port | `6379` |

`DB_PORT` is `5433` rather than `5432` on purpose: if you have PostgreSQL installed on
Windows, it takes 5432 and DBeaver or the API would connect to it instead of the container
(symptom: `password authentication failed for user "myuser"`).

The user and password are set **the first time** the Postgres volume is created. If you
change them later, you have to recreate it: `docker compose ... down -v` (deletes the data).

### 2. Database and Redis

From the repository root:

```powershell
docker compose --env-file .env -f app/docker-compose.yml up -d
docker ps        # postgres_db and redis_cache should be "Up"
```

`--env-file .env` is required because the compose file is in `app/` and `.env` is at the root.

### 3. Python dependencies

```powershell
cd app
uv sync
```

Creates `app/.venv` with Python 3.12 and everything in `uv.lock`. No need to activate the
environment: `uv run <command>` uses it automatically.

### 4. Create the tables

```powershell
uv run python -m db.init_db
```

Development shortcut that creates the tables from the models. Board task WTA-4 calls for
replacing it with Alembic migrations (see "Next steps").

### 5. Run the API

```powershell
uv run uvicorn main:app --reload
```

- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/Health

### 6. Run the worker

In another terminal, also from `app/`:

```powershell
uv run python -m worker.worker
```

It picks up the jobs enqueued by `POST /jobs` one at a time and runs `worker/tasks.py`
(`queued` → `running` → `done`/`failed`). Without a worker
running, jobs stay `queued`.

### Alternative: everything in Docker

Instead of steps 3–6, build and run API and worker as containers next to Postgres and Redis.
From the repository root:

```powershell
docker compose --env-file .env -f app/docker-compose.yml up -d --build
docker compose --env-file .env -f app/docker-compose.yml exec api python -m db.init_db          # first time only
docker compose --env-file .env -f app/docker-compose.yml exec worker python -m worker.check_browser  # Playwright opens example.com
docker compose --env-file .env -f app/docker-compose.yml logs -f worker                         # watch jobs being processed
```

`app/Dockerfile` has two targets from the same base: `api` (slim) and `worker` (adds Chromium
only, via `playwright install --with-deps chromium`). To run the worker or the Playwright
tests **outside** Docker, download the same browser once: `uv run playwright install chromium`
(from `app/`; the build must match the Playwright version in `uv.lock`, so rerun it after
`uv sync` upgrades Playwright).

If you created the tables before the `elements` column existed, add it without losing data:
`docker exec -it postgres_db psql -U myuser -d local -c "ALTER TABLE pages ADD COLUMN elements JSONB;"`
(Alembic will handle this once WTA-4 is done). Inside the Docker network the compose
file overrides `ENDPOINT=postgres`, `REDIS_HOST=redis` and `DB_PORT=5432`; your `.env` keeps
the `localhost` values for running outside Docker.

## API usage

| Method | Path | Response |
|---|---|---|
| `POST` | `/jobs` | `202` with the created job; `400` if the URL is not http/https, does not resolve, or points to an internal network (anti-SSRF) |
| `GET` | `/jobs` | list of jobs (newest first), optional `limit` and `offset` |
| `GET` | `/jobs/{id}` | the job; `404` if it does not exist; `422` if the id is not a UUID |
| `GET` | `/jobs/{id}/pages` | pages crawled for the job |
| `GET` | `/jobs/{id}/scenarios` | scenarios generated for the job |
| `DELETE` | `/jobs/{id}` | `204`; also deletes pages and scenarios |

```powershell
# create a job
$job = Invoke-RestMethod -Method Post http://127.0.0.1:8000/jobs `
  -ContentType "application/json" -Body '{"url": "https://example.com"}'

# fetch it
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($job.id)"
```

## Verify and debug

```powershell
# container status
docker ps
docker logs postgres_db --tail 20

# Postgres and Redis respond
docker exec -it postgres_db pg_isready -U myuser -d local
docker exec -it redis_cache redis-cli ping          # PONG

# raw SQL
docker exec -it postgres_db psql -U myuser -d local -c "SELECT id, url, status FROM job;"

# drop all tables and start from scratch
docker exec -it postgres_db psql -U myuser -d local -c "DROP TABLE scenarios, pages, job; DROP TYPE job_status;"
```

**DBeaver**: new PostgreSQL connection with host `localhost`, port `5433`, database `local`,
user `myuser`, password `password`.

**Shut everything down**: `docker compose --env-file .env -f app/docker-compose.yml down`
(add `-v` to also delete the data).

## Development

### VS Code

The Python project lives in `app/`, not at the root, so VS Code does not detect the `.venv`
on its own. `pyrightconfig.json` and `.vscode/settings.json` already point to it; if imports
still show in red: `Ctrl+Shift+P` → *Python: Select Interpreter* → *Enter interpreter path* →
`app\.venv\Scripts\python.exe`, then *Developer: Reload Window*.

### Adding dependencies

```powershell
cd app
uv add package-name      # updates pyproject.toml and uv.lock
```

Commit `pyproject.toml` and `uv.lock` together.

### Conventions

- Functions in `db_crud.py` receive the session as a parameter (`db: Session`); they never
  use a global session. Routes get it with `Depends(get_db)`.
- API input and output always go through Pydantic schemas (`models/models.py`); SQLAlchemy
  models are never exposed directly.
- A job's `status` is the `JobStatus` enum, not free text.
- Counter operations (`pages_crawled`, `total_scenarios`) use an atomic `UPDATE` in the same
  transaction as the insert, with one commit per page.
- `create_engine(..., echo=True)` prints SQL to the console; turn it off if it gets noisy.

## Next steps (per the board)

1. **Alembic** (closes WTA-4):
   ```powershell
   cd app
   uv add alembic
   uv run alembic init alembic
   ```
   In `alembic/env.py`: import `Base` and `DATABASE_URL` from `db.conn`, import
   `db.models_db.models_db`, and set `target_metadata = Base.metadata`. Then
   `uv run alembic revision --autogenerate -m "initial tables"` and
   `uv run alembic upgrade head`. If you already created tables with `init_db`, drop them first.
2. **RQ + worker** (WTA-6 to 8): `uv add rq`; the worker uses `mark_job_running`,
   `mark_job_done` and `mark_job_failed` from `db_crud.py`.
3. **Explorer** (WTA-12, 13): screenshots and `page_type` classification. Add them in
   `PlaywrightFetcher.fetch` (the page is already open there) and carry them on
   `CrawledPage` / `FetchResult`; `persist_page` in the worker passes them to `create_page`.
4. **Designer** (WTA-15 to 18): `create_scenarios` already generates the `SC-XXX` codes.
