import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from db.models_db.models_db import JobStatus


class JobCreate(BaseModel):
    url: HttpUrl = Field(..., description="The url for the test execution")


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
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
