# app — backend (FastAPI + SQLAlchemy)

Overview and quick start: [repository root README](../README.md).
Full documentation (setup, environment variables, API, conventions): [docs/guide.md](../docs/guide.md).

Quick commands, from this folder:

```powershell
uv sync                          # dependencies
uv run python -m db.init_db      # create tables
uv run uvicorn main:app --reload # API at http://127.0.0.1:8000/docs
```
