import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from config.languages import DEFAULT_LANGUAGE, Language
from db.models_db.models_db import JobStatus, RunStatus


# ------------------------------------------------------------------ auth (WTA-26)

class UserCredentials(BaseModel):
    """Body of POST /auth/register and POST /auth/login."""

    email: EmailStr
    # argon2 has no practical length limit, the cap only keeps absurd bodies out
    password: str = Field(..., min_length=8, max_length=128, description="At least 8 characters")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    email_verified: bool = Field(default=False, description="Whether the verification link was clicked")
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _verified_from_timestamp(cls, data):
        # The ORM row has `email_verified_at`; the API shows a plain boolean
        if hasattr(data, "email_verified_at"):
            return {"id": data.id, "email": data.email, "created_at": data.created_at,
                    "email_verified": data.email_verified_at is not None}
        return data


class PasswordConfirm(BaseModel):
    """Body of DELETE /auth/me: the account's password, as a confirmation."""

    password: str = Field(..., min_length=1, max_length=128)


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    jobs_per_month: int
    price_usd: int
    test_runs_per_month: int = 0


class BillingInfo(BaseModel):
    """GET /billing/me (WTA-30): the plan in force, this month's usage and how to upgrade."""

    plan: PlanRead
    used_this_month: int
    remaining: int
    runs_this_month: int = 0
    runs_remaining: int = 0
    status: str | None = Field(None, description="The provider's subscription status, if any")
    current_period_end: datetime | None = None
    customer_portal_url: str | None = Field(None, description="Where the user manages or cancels the subscription")
    plans: list[PlanRead]
    checkout_urls: dict[str, str] = Field(description="Hosted checkout per plan code, when billing is configured")


class EmailRequest(BaseModel):
    """Body of POST /auth/password/forgot."""

    email: EmailStr


class TokenRequest(BaseModel):
    """Body of POST /auth/verify: the token from the link in the email."""

    token: str = Field(..., min_length=10, max_length=200)


class PasswordReset(BaseModel):
    """Body of POST /auth/password/reset."""

    token: str = Field(..., min_length=10, max_length=200)
    password: str = Field(..., min_length=8, max_length=128)


class Token(BaseModel):
    """What register and login return: the JWT for `Authorization: Bearer <access_token>`."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")
    user: UserRead


class JobLogin(BaseModel):
    """Credentials for a site whose content is behind a login (WTA-29). Used once, for this
    job, then deleted. Use a TEST account on a TEST environment: the explorer browses as it."""

    login_url: str | None = Field(None, max_length=2048, description="The login page; defaults to the job URL")
    username: str = Field(..., min_length=1, max_length=320)
    password: str = Field(..., min_length=1, max_length=256)
    authorized: bool = Field(..., description="Must be true: you confirm you are allowed to test this site with this account")


class JobCreate(BaseModel):
    # str rather than HttpUrl: security.ssrf does the validation so we can return 400 (not 422)
    url: str = Field(..., max_length=2048, description="The url for the test execution")
    description: str | None = Field(None, max_length=500, description="Optional description for the test execution")
    language: Language = Field(
        DEFAULT_LANGUAGE, description="Language of the scenarios and the report: 'en' (English) or 'es' (Spanish)"
    )
    login: JobLogin | None = Field(None, description="Optional: log in before exploring")
    select_pages: bool = Field(
        False, description="Explore first (up to 20 pages), then wait for you to choose which pages get scenarios",
    )


class RunCreate(BaseModel):
    """Body of POST /jobs/{id}/runs (WTA-33): the Executor acts on the site, so the user
    confirms it is a test environment; credentials are optional and used once."""

    test_environment: bool = Field(..., description="Must be true: the site is a test environment, not production")
    login: JobLogin | None = None


class ScenarioResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario_id: int
    outcome: str
    reason: str
    steps: list[Any] = []
    has_screenshot: bool = False
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _flag_screenshot(cls, data):
        if hasattr(data, "screenshot_path"):
            return {"id": data.id, "scenario_id": data.scenario_id, "outcome": data.outcome, "reason": data.reason,
                    "steps": list(data.steps or []), "has_screenshot": bool(data.screenshot_path), "created_at": data.created_at}
        return data


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    status: RunStatus
    total: int
    passed: int
    failed: int
    blocked: int
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    results: list[ScenarioResultRead] = []


class PageSelection(BaseModel):
    """Body of POST /jobs/{id}/pages/select (WTA-32)."""

    page_ids: list[int] = Field(..., min_length=1, max_length=200)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str | None = None
    language: Language = DEFAULT_LANGUAGE
    url: str
    login_url: str | None = Field(None, description="Set when the job explored the site logged in (credentials are never returned)")
    select_pages: bool = False
    status: JobStatus
    pages_crawled: int
    total_scenarios: int
    report_path: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    url: str
    title: str | None = None
    page_type: str | None = None
    screenshot_path: str | None = None
    elements: dict[str, Any] | None = None
    selected: bool = True
    created_at: datetime


class ScenarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    page_id: int
    scenario_code: str
    title: str
    steps: list[Any]
    expected_result: str
    priority: str | None = None
    category: str | None = None


class ModelUsage(BaseModel):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float
    calls_without_price: int = Field(description="Calls served by a model with no known price; not in cost_usd")


class JobUsage(BaseModel):
    """LLM tokens and estimated USD a job has spent (WTA-18), from the llm_calls table."""

    job_id: uuid.UUID
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float = Field(description="List-price estimate in USD")
    calls_without_price: int
    by_model: list[ModelUsage]
