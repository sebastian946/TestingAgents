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

settings = Settings()  # type: ignore[call-arg]  # values come from .env
