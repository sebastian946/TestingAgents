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
| C — Explorer | Full-page screenshots per page (WTA-12) | Done |
| C — Explorer | Heuristic `page_type` classification (WTA-13) | Done |
| C — Explorer | Persist pages one by one, live progress in `GET /jobs/{id}` (WTA-14) | Done |
| D — Designer | Designer prompt + structured output with Pydantic (WTA-15) | Done (not yet wired into the worker) |
| D — Designer | Parse retry, LLM error handling and model choice (WTA-16) | Done; model comparison pending a real API key ([ADR-02](docs/adr/ADR-02-designer-model.md)) |
| D — Designer | Designer wired into the worker, scenarios persisted in Postgres (WTA-17) | Done |
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

## Querying the scenarios (WTA-17)

After the crawl, the worker sends each page to the Designer and stores its scenarios in
`scenarios`, one short transaction per page, so `total_scenarios` grows live like
`pages_crawled`. Codes `SC-001`, `SC-002`... continue across the job's pages and are unique
per job (constraint `uq_scenario_code_per_job`). A page the Designer cannot cover is skipped:
the job still ends `done`, and `job.error` lists the uncovered pages. If no page gets
scenarios, or the configuration is wrong (bad API key, unknown model), the job ends `failed`.

`priority` and `category` are plain columns because they are what you filter and group by.
`steps` is JSONB because it is an ordered list, always read whole with its scenario, and its
length varies per scenario. The scenarios also come back from `GET /jobs/{id}/scenarios`.

```sql
-- the counter matches the rows, and the codes are unique
SELECT j.total_scenarios, count(s.id) AS rows, count(DISTINCT s.scenario_code) AS unique_codes
FROM job j LEFT JOIN scenarios s ON s.job_id = j.id
WHERE j.id = '<job_id>' GROUP BY j.total_scenarios;

-- by page
SELECT p.url, p.page_type, count(s.id) AS scenarios
FROM pages p JOIN scenarios s ON s.page_id = p.id
WHERE p.job_id = '<job_id>' GROUP BY p.url, p.page_type;

-- by priority / by category
SELECT priority, count(*) FROM scenarios WHERE job_id = '<job_id>' GROUP BY priority;
SELECT category, count(*) FROM scenarios WHERE job_id = '<job_id>' GROUP BY category;

-- the steps of one scenario, one row per step (JSONB)
SELECT jsonb_array_elements_text(steps) AS step
FROM scenarios WHERE job_id = '<job_id>' AND scenario_code = 'SC-001';
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

### Designer agent (`agents/designer.py`)

`design_scenarios(page, description)` sends one page (its `page_type` and `PageInfo`
inventory) plus the app description from the job to Claude and returns validated
`TestScenario`s (title, steps, expected_result, priority `critical/high/medium/low`,
category `functional/negative/validation/security/usability/accessibility`).

- **Prompts are versioned files**, not strings in code: `agents/prompts/designer/vN/`
  (`system.md` static, `user.md` per page). `DESIGNER_PROMPT_VERSION` selects one; the
  history and rationale of each version is in `agents/prompts/designer/CHANGELOG.md`.
- **Structured outputs**: the request carries the `DesignerOutput` JSON schema
  (`output_config.format`), and Pydantic validates the response on our side. That second
  check covers the rules the API cannot enforce: non-blank text, at least one step, unique
  titles, at most 12 scenarios, titles that fit the column.
- **Self-healing retry** (WTA-16): a response that fails validation is sent back once, with
  the errors as feedback ("field X failed because Y, correct it"). If it fails again, that
  page has no scenarios.
- **API errors** (WTA-16): the SDK retries rate limits (429), overload (529), 5xx and
  timeouts with exponential backoff (`DESIGNER_MAX_RETRIES`, `DESIGNER_TIMEOUT`). After
  that, the page is skipped (`DesignerError`). Configuration errors (bad key, unknown model)
  raise `DesignerFatalError` and stop the run, since every page would fail the same way.
- **One page never stops the job**: `design_pages(pages, description)` returns one
  `PageDesign` per page, with either its scenarios or the error that left it empty.
- **Model choice**: `DESIGNER_MODEL` is configuration, and the request only sends what each
  model accepts (Haiku 4.5 has no `effort`). The decision and its cost analysis are in
  [ADR-02](docs/adr/ADR-02-designer-model.md).
- **No `temperature`**: current Claude models reject sampling parameters (the board's
  "temperature 0.2" predates that). Consistency comes from the schema, the rubric in the
  prompt and `DESIGNER_EFFORT`.
- **Prompt caching** on the static system prompt; it only takes effect once the prefix
  reaches the model's minimum cacheable size (check `cache_read_input_tokens` in the eval).
- **Refusal fallback** (`fallbacks="default"`): if a safety classifier declines a request,
  the API retries it on Anthropic's recommended fallback model within the same call.

Needs `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). Evaluate a prompt version against
the WTA-15 acceptance criteria — it calls the real API, asks before spending, and saves the
results to `reports/designer_eval/`:

```powershell
cd app
uv run python -m agents.designer_eval --version v2
uv run python -m agents.designer_eval --model claude-sonnet-5-5   # same pages, another model
uv run python -m agents.designer_eval --compare                   # saved runs side by side, no API calls
```

### Page type rules (`agents/page_classifier.py`)

`page_type` is decided by deterministic rules over the `PageInfo` inventory plus a few
counters the extractor computes in the browser (`PageInfo.signals`: `price_count`,
`stock_words`, `link_count`, `max_similar_links`, `word_count`, `repeated_button_max`).
No LLM: it is cheaper, faster, reproducible, and good enough for the MVP. First match wins:

| # | `page_type` | Rule |
|---|---|---|
| 1 | `login` | a form has a `password` field and looks like sign-in (≤ 3 fields, or "log in / sign in" wording) |
| 2 | `signup` | password field plus > 3 fields, a confirm-password field, or "sign up / register / create account" wording |
| 3 | `checkout` | card fields (`card`, `cvv`, `expiry`…) or "checkout / pay / place order" wording |
| 4 | `product` | 1–5 prices and either a buy/add-to-cart button or product vocabulary ("in stock", "sku", "quantity"), with no button repeated ≥ 4 times |
| 5 | `listing` | ≥ 6 prices, or the same button text ≥ 4 times (a grid of cards), or ≥ 6 links sharing a path prefix with < 6 words per link, or a path like `/products`, `/catalogue`, `/category`, `/blog`, `/search` |
| 6 | `form` | a form with ≥ 2 visible fields that is not just a search box |
| 7 | `content` | everything else (home, articles, about pages, search-only pages) |

Fields found outside any `<form>` (JS-driven logins) are grouped into a *virtual form*
(`virtual: true`), so rule 1 still applies. Thresholds are constants at the top of the
module. Measured on 9 real pages (2 logins, 2 forms, 1 product, 2 listings, 2 content
pages): 9/9 correct; the signals that mattered are in `tests/test_page_classifier.py`.
To improve a rule, add the failing page as a unit test first, then adjust the threshold.
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
3. **Failure handling** (WTA-8): reprocessing a job is already safe (`mark_job_running`
   wipes the previous attempt's pages and counters), so RQ retries can be enabled.
4. **Designer** (WTA-18): log the token usage and `estimated_cost_usd` of each
   `DesignResult` per job; today the worker only prints them. Run the model comparison and
   close ADR-02, then write prompt v3. The first real job on a login page produced 11
   scenarios, against the prompt's 5 to 8, and 9 of them `critical`, because the description
   raised every priority one level. v3 should keep the counts within the guidance and leave
   some spread in the priorities.
