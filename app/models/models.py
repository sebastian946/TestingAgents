from pydantic import BaseModel, Field


class Jobs(BaseModel):
    id: str = Field(..., description="The unique identifier for the job execution")
    url: str = Field(..., description="The url for the test execution")
    status: str = Field(..., description="The status of the job execution")
    test_scenarios: list[str] = Field(default_factory=list, description="Test scenarios associated with the job execution")


class JobCreate(BaseModel):
    url: str = Field(..., min_length=1, description="The url for the test execution")
