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

    # Designer agent (WTA-15). Optional so the API, the crawl and the tests run without a key;
    # the Designer raises a clear error if it is called without one.
    anthropic_api_key: str | None = None
    designer_model: str = "claude-opus-5-5"
    designer_effort: str = "high"  # low | medium | high | xhigh | max
    designer_prompt_version: str = "v2"  # folder under agents/prompts/designer/
    # WTA-16: the SDK retries 429/529/5xx/timeouts with exponential backoff up to this many
    # times; the timeout is per attempt (high effort on a big page can take minutes)
    designer_max_retries: int = 4
    designer_timeout: float = 300.0

settings = Settings()  # type: ignore[call-arg]  # values come from .env
