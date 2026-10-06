# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

SaaS for agent-assisted web testing: takes a site URL, crawls it, generates test scenarios with an LLM, and produces a report. The repo currently holds "Paso 1 (Motor MVP)": FastAPI API + PostgreSQL + (planned) Redis/RQ worker. The task board lives in Notion (link in README.md); task IDs like WTA-4 refer to it. Code comments, docstrings, error messages, and docs are written in English (the Notion board itself is in Spanish).

## Commands

The Python project lives in `app/`, not the repo root. **Run Python commands from `app/`**: imports are rooted there (`from db.conn import ...`), and `config/variables.py` loads `env_file="../.env"` relative to the working directory, so running from elsewhere fails to find settings.

```bash
# From repo root: Postgres 16 (host port 5433) + Redis 7. --env-file is required because the compose file is in app/ and .env is at the root.
docker compose --env-file .env -f app/docker-compose.yml up -d

cd app
uv sync                            # install deps into app/.venv (Python 3.12)
uv run python -m db.init_db        # create tables from models (dev shortcut; Alembic is planned, WTA-4)
uv run uvicorn main:app --reload   # API at http://127.0.0.1:8000/docs, health at /Health
uv run python -m worker.worker     # RQ worker (separate terminal); runs worker/tasks.py
uv add <pkg>                       # add a dependency; commit pyproject.toml and uv.lock together
uv run playwright install chromium # once per machine; the Playwright tests and the worker need the browser build matching uv.lock
uv run pytest                      # tests (app/tests/); they spin up a local HTTP server, no DB or internet needed
uv run pytest tests/test_explorer.py -k robots   # single file / test

# Fully containerized alternative (from repo root). Dockerfile targets: `api` (slim) and `worker` (adds Chromium only).
docker compose --env-file .env -f app/docker-compose.yml up -d --build
docker compose --env-file .env -f app/docker-compose.yml exec worker python -m worker.check_browser   # Playwright smoke test
```

Tests use pytest (`app/tests/`, run from `app/`). No linter or formatter is configured yet. Pyright/Pylance is pointed at `app/.venv` via the root `pyrightconfig.json`.

`.env` is copied from `.env.example`. `DB_PORT` is 5433 on purpose to avoid colliding with a locally installed Postgres. Postgres credentials are fixed when the volume is first created; changing them requires `docker compose ... down -v`.

## Architecture

Async job pattern: `POST /jobs` only inserts a `job` row with status `queued` and returns 202. The actual work (crawl, LLM, report) will be done by a separate RQ worker process; clients poll `GET /jobs/{id}`. API and worker share state **only through the database**, never in memory.

Layers (all under `app/`):
- `main.py` — FastAPI app, CORS, `/Health`, includes the router.
- `routes/routes.py` — `/jobs` endpoints; get a session via `Depends(get_db)` and delegate to `db_crud`.
- `models/models.py` — Pydantic request/response schemas (`*Read` use `from_attributes=True`). SQLAlchemy models are never returned directly.
- `db/conn.py` — engine (`echo=True`, logs SQL), `local_session`, `Base`, `get_db` dependency.
- `db/models_db/models_db.py` — SQLAlchemy 2.0 typed models: `job` (UUID PK) → 1:N `pages` → 1:N `scenarios`; deleting a job cascades. `status` is the `JobStatus` enum (Postgres type `job_status`). `steps` uses a JSON/JSONB variant so tests can run on SQLite.
- `db/db_crud.py` — all DB operations. Every function takes `db: Session` as a parameter (no global session). Already contains the worker-facing functions for upcoming tasks: `mark_job_running` / `mark_job_done` / `mark_job_failed` (state transitions + timestamps), `create_page` (also increments `pages_crawled`), `create_scenarios` (assigns per-job `SC-001`… codes and increments `total_scenarios`).

Conventions in `db_crud.py`: counters are incremented with an atomic SQL expression (`Job.pages_crawled + 1`) in the same transaction as the insert, and the explorer commits once per page so partial crawls stay consistent if the worker dies.

- `db/redis/redis_conn.py` — Redis client + RQ queue. `add_new_job_to_queue` enqueues the task by import string (`"worker.tasks.process_url_task"`) so the API never imports worker code (avoids a circular import). Only the job id travels through Redis.
- `worker/worker.py` — starts the RQ worker; `worker/tasks.py` — `process_url_task` opens its own DB session, drives `queued → running → done/failed`, and runs the Explorer, persisting each page through an `on_page` callback (`db_crud.create_page`, one commit per page). The Designer/Documenter agents will be added after the crawl.
- `agents/explorer.py` — `crawl(start_url, max_pages=8, timeout=15, on_page=None, use_browser=False)`: deterministic same-domain BFS (ADR-01: no LLM here). One `_bfs` loop, two fetchers: `RequestsFetcher` (static HTML, default, used by most tests) and `PlaywrightFetcher` (`use_browser=True`, what the worker uses): one headless Chromium per crawl, images/fonts/media blocked, `domcontentloaded` + best-effort 3s `networkidle`, and it fills `CrawledPage.elements`. Guardrails: anti-SSRF check on every URL before fetching, robots.txt via `protego` (the stdlib `urllib.robotparser` ignores wildcards, which most real sites use), `User-Agent: WebTestAgent/0.1`, per-page timeout that skips the page rather than aborting, non-HTML responses skipped, off-site redirects dropped. `normalize_url` drops fragment and query string on purpose (`/items?page=2` counts as `/items`).
- `agents/page_info.py` — the `PageInfo` inventory stored in `pages.elements` (JSONB) and later fed to the Designer instead of raw HTML: forms (fields with tag/type/name/id/label/placeholder/required/options, submit text), buttons outside forms, main nav links, h1/h2. Extracted by ONE `page.evaluate(EXTRACT_JS)` returning plain JSON (never DOM handles or `outerHTML`); hidden elements, scripts and styles are skipped, every list is capped and `to_dict()` drops empty values so a page stays well under 2KB. Label lookup handles `for`, wrapping labels, `aria-label(ledby)`, the React `<id>-label` convention and a sibling label in the same group; a `<button type="button">` inside a form counts as its submit when no real submit exists. Fields outside any `<form>` become one *virtual form* (`virtual: true`, JS-driven logins); buttons of forms without fields (bare "Add to basket") are listed as page buttons. `PageInfo.signals` carries counters for classification (`price_count`, `stock_words`, `link_count`, `max_similar_links`, `word_count`, `repeated_button_max`).
- `agents/page_classifier.py` — `classify_page(info) -> PageType` (login, signup, checkout, product, listing, form, content), pure function, rules ordered first-match-wins and documented in the module docstring and README ("Page type rules"). Thresholds are module constants. `PlaywrightFetcher.fetch` stores the value in `FetchResult.page_type` → `CrawledPage.page_type` → `pages.page_type`; `RequestsFetcher` leaves it `None`. When a real page is misclassified: add it to `tests/test_page_classifier.py` as a unit case with its signals, then tune.

Inside Docker, hosts are service names: compose overrides `ENDPOINT=postgres`, `REDIS_HOST=redis`, `DB_PORT=5432`; `.env` keeps `localhost`/`5433` for running outside Docker.

Planned next steps (per README): Alembic migrations (WTA-4), failure handling/retries (WTA-8), then Explorer / Designer / Documenter agents.
