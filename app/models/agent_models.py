"""Structured output schema for the Designer agent (WTA-15).

The Designer's response is constrained to `DesignerOutput` through the API's structured
outputs (`messages.parse(output_format=DesignerOutput)`), so it always parses; Pydantic
validates it again on our side. Field limits match the `scenarios` table (WTA-17).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["critical", "high", "medium", "low"]
Category = Literal["functional", "negative", "validation", "security", "usability", "accessibility"]


class TestScenario(BaseModel):
    # Not a pytest test class, despite the name
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Short, specific name of what is verified, e.g. 'Login fails with a wrong password'")
    steps: list[str] = Field(
        description="Ordered steps a QA engineer can execute, one action per step, with the concrete test data to use"
    )
    expected_result: str = Field(description="Observable outcome that decides pass/fail")
    priority: Priority = Field(description="Business impact if this scenario fails (see the priority rubric)")
    category: Category = Field(description="Kind of check this scenario performs")


class DesignerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios: list[TestScenario] = Field(description="Test scenarios for this page, most important first")
