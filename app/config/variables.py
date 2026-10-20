import os
from typing import Literal

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    db_user: str
    db_password: str
    db_name: str
    db_port: int = 5432
    redis_port: int = 6379
    redis_host: str = "localhost"
    endpoint: str
    # Root folder for per-job files (screenshots, reports). Relative to app/; in Docker it
    # is /app/reports, backed by the `reports_data` volume shared by api and worker.
    reports_dir: str = "reports"
    # RQ queue the API enqueues to and the worker listens on. The integration test uses its
    # own name, so a developer's worker on the same Redis never steals its jobs (WTA-24)
    rq_queue_name: str = "default"
    # Hostnames the anti-SSRF check lets through even if they are internal. TEST ONLY: the
    # integration test serves its site from 127.0.0.1. Empty in every real deployment.
    ssrf_allowed_hosts: list[str] = []
    # Redis AUTH password; None for the local compose, set by docker-compose.prod.yml (WTA-26)
    redis_password: str | None = None
    # SQLAlchemy echo: every SQL statement (LLM text included) goes to the console. Handy
    # locally, noise and disk usage in production, where the prod compose turns it off
    sql_echo: bool = True
    # Browser origins allowed by CORS. "*" for local development; in production the exact
    # origin of the frontend, e.g. ["https://app.example.com"]
    cors_origins: list[str] = ["*"]

    # Authentication (WTA-26): users sign up with email + password and get a JWT (HS256)
    # signed with this key. Generate one with
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    # Changing it logs every user out (their tokens stop verifying); nothing else is lost.
    # Empty here so the worker and the tests do not need it; the API refuses to start without it
    auth_secret_key: str = ""
    auth_token_expire_minutes: int = 60 * 24  # one day
    # Jobs a user may create per rolling hour: each one costs real LLM money (ADR-06)
    auth_max_jobs_per_hour: int = 20
    # Lifetimes of the links sent by email (WTA-28)
    auth_verify_token_hours: int = 48
    auth_reset_token_minutes: int = 60

    # Fernet key that encrypts the login credentials a user gives for one job (WTA-29).
    # Optional: without it, jobs with a login are refused. Generate with
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credentials_key: str | None = None

    # Email (WTA-28): verification links and password resets. "console" prints the message
    # to the API log (development, tests); "resend" sends it through https://resend.com
    email_backend: Literal["console", "resend"] = "console"
    resend_api_key: str | None = None
    email_from: str = "Testing Agents <onboarding@resend.dev>"
    # Public URL of the dashboard, where the links in emails point to (no trailing slash)
    app_url: str = "http://localhost:5173"

    # Designer agent (WTA-15). Optional so the API, the crawl and the tests run without a key;
    # the Designer raises a clear error if it is called without one.
    anthropic_api_key: str | None = None
    designer_model: str = "claude-opus-5-5"
    # "anthropic" (real Claude) or "fake" (deterministic scenarios from the inventory, no
    # API call, no cost). Fake exists for the end-to-end integration test (WTA-24)
    designer_mode: Literal["anthropic", "fake"] = "anthropic"
    designer_effort: str = "high"  # low | medium | high | xhigh | max
    designer_prompt_version: str = "v3"  # folder under agents/prompts/designer/
    # WTA-16: the SDK retries 429/529/5xx/timeouts with exponential backoff up to this many
    # times; the timeout is per attempt (high effort on a big page can take minutes)
    designer_max_retries: int = 4
    designer_timeout: float = 300.0

    # LangSmith tracing (optional). The SDK only reads os.environ, so these are exported
    # below: pydantic-settings reads .env into this object but never into the environment
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "TestingAgents"
    langsmith_endpoint: str = "https://api.smith.langchain.com"


def export_langsmith_env(s: "Settings") -> bool:
    """Make the LangSmith settings visible to the SDK; return whether tracing is on.

    Values already in the environment win (Docker's env_file, or tests that force tracing
    off), so this only fills what is missing. Tracing without an API key is turned off
    instead of failing on every traced call.
    """
    if s.langsmith_tracing and not s.langsmith_api_key:
        print("LANGSMITH_TRACING is true but LANGSMITH_API_KEY is empty: tracing disabled.")
        os.environ["LANGSMITH_TRACING"] = "false"
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true" if s.langsmith_tracing else "false")
    if s.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", s.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", s.langsmith_project)
    os.environ.setdefault("LANGSMITH_ENDPOINT", s.langsmith_endpoint)
    # Any other LANGSMITH_* option in .env (LANGSMITH_HIDE_INPUTS, ..._TRACING_SAMPLING_RATE)
    # reaches the SDK too, without a field here for each one
    env_file = s.model_config.get("env_file")
    if isinstance(env_file, str) and os.path.isfile(env_file):
        for key, value in dotenv_values(env_file).items():
            if key.upper().startswith("LANGSMITH_") and value is not None:
                os.environ.setdefault(key.upper(), value)
    return os.environ["LANGSMITH_TRACING"].lower() == "true"


settings = Settings()  # type: ignore[call-arg]  # values come from .env
TRACING_ENABLED = export_langsmith_env(settings)
