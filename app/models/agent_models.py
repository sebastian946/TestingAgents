"""Structured output schema for the Designer agent (WTA-15, WTA-16).

The API constrains the Designer's response to this JSON schema (structured outputs). Some
rules cannot be expressed or enforced that way (non-blank text, unique titles, a sane number
of scenarios, lengths that fit the `scenarios` table), so Pydantic checks them on our side;
a response that breaks them is sent back to the model once with the validation errors as
feedback (see `agents.designer`).
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Priority = Literal["critical", "high", "medium", "low"]
Category = Literal["functional", "negative", "validation", "security", "usability", "accessibility"]

MAX_SCENARIOS_PER_PAGE = 12
MAX_TITLE_LENGTH = 255  # scenarios.title is String(255)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TestScenario(BaseModel):
    # Not a pytest test class, despite the name
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    title: Annotated[NonBlank, StringConstraints(max_length=MAX_TITLE_LENGTH)] = Field(
        description="Short, specific name of what is verified, e.g. 'Login fails with a wrong password'"
    )
    steps: list[NonBlank] = Field(
        min_length=1,
        description="Ordered steps a QA engineer can execute, one action per step, with the concrete test data to use",
    )
    expected_result: NonBlank = Field(description="Observable outcome that decides pass/fail")
    priority: Priority = Field(description="Business impact if this scenario fails (see the priority rubric)")
    category: Category = Field(description="Kind of check this scenario performs")


class DesignerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios: list[TestScenario] = Field(
        min_length=1,
        max_length=MAX_SCENARIOS_PER_PAGE,
        description="Test scenarios for this page, most important first",
    )

    @field_validator("scenarios")
    @classmethod
    def titles_are_unique(cls, scenarios: list[TestScenario]) -> list[TestScenario]:
        seen: set[str] = set()
        for scenario in scenarios:
            key = scenario.title.casefold()
            if key in seen:
                raise ValueError(f"duplicate scenario title: {scenario.title!r}; each scenario must verify a different behavior")
            seen.add(key)
        return scenarios
