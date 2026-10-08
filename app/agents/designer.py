"""Designer agent: turns a crawled page into test scenarios with Claude (WTA-15).

One API call per page. The prompt lives in versioned files (``agents/prompts/designer/vN``),
never in code; the response is constrained to ``DesignerOutput`` with structured outputs, so
it always parses and is validated by Pydantic.

API choices:
- Model, effort and prompt version come from settings (``DESIGNER_*`` in .env).
- No ``temperature``: current Claude models reject sampling parameters. Consistency comes
  from the schema, the priority rubric in the prompt and the effort level instead.
- The system prompt is static and marked for prompt caching; everything that changes per
  page goes in the user message, so the cached prefix is reused across pages and jobs.
- ``fallbacks="default"``: if a safety classifier declines the request, the API re-runs it
  on Anthropic's recommended fallback model inside the same call.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any, Protocol

import anthropic

from config.variables import settings
from models.agent_models import DesignerOutput, TestScenario

PROMPTS_DIR = Path(__file__).parent / "prompts" / "designer"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
NO_DESCRIPTION = "(not provided)"
# Counters used by the page classifier: noise for the LLM, and they cost tokens
INVENTORY_DROP_KEYS = {"signals", "url", "title"}


class DesignerError(RuntimeError):
    """The Designer could not produce scenarios for a page. The message is safe to store."""


class PageLike(Protocol):
    url: str
    title: str | None
    page_type: str | None
    elements: dict[str, Any] | None


@dataclass
class DesignResult:
    scenarios: list[TestScenario]
    model: str  # model that actually served the response (differs if a fallback ran)
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


@lru_cache
def load_prompt(version: str) -> tuple[str, Template]:
    """Return (system prompt, user message template) for a prompt version."""
    folder = PROMPTS_DIR / version
    if not folder.is_dir():
        available = sorted(p.name for p in PROMPTS_DIR.iterdir() if p.is_dir())
        raise DesignerError(f"Unknown designer prompt version {version!r}; available: {available}")
    system = (folder / "system.md").read_text(encoding="utf-8").strip()
    user = Template((folder / "user.md").read_text(encoding="utf-8").strip())
    return system, user


def build_inventory(elements: dict[str, Any] | None) -> str:
    """Compact JSON of the PageInfo inventory the model sees (no classifier counters)."""
    clean = {k: v for k, v in (elements or {}).items() if k not in INVENTORY_DROP_KEYS}
    if not clean:
        return "(no interactive elements were found on this page)"
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_prompt(page: PageLike, description: str | None, version: str) -> tuple[str, str]:
    system, user_template = load_prompt(version)
    user = user_template.substitute(
        url=page.url,
        title=page.title or "(no title)",
        page_type=page.page_type or "unknown",
        description=(description or "").strip() or NO_DESCRIPTION,
        inventory=build_inventory(page.elements),
    )
    return system, user


def _default_client() -> anthropic.Anthropic:
    # With no key in .env the SDK still resolves ANTHROPIC_API_KEY from the environment
    # or an `ant auth login` profile
    if settings.anthropic_api_key:
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return anthropic.Anthropic()


def design_scenarios(
    page: PageLike,
    description: str | None = None,
    *,
    client: anthropic.Anthropic | None = None,
    version: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> DesignResult:
    """Generate test scenarios for one crawled page.

    `page` is anything with url/title/page_type/elements: a `CrawledPage`, a `Page` row or
    a `PageRead`. `description` is the app description the user gave when creating the job.
    """
    system, user = render_prompt(page, description, version or settings.designer_prompt_version)
    client = client or _default_client()
    try:
        response = client.beta.messages.parse(
            model=model or settings.designer_model,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort or settings.designer_effort},
            output_format=DesignerOutput,
        )
    except anthropic.AuthenticationError as exc:
        raise DesignerError("The Anthropic API key is missing or invalid (ANTHROPIC_API_KEY in .env).") from exc
    except anthropic.BadRequestError as exc:
        raise DesignerError(f"The Designer request was rejected: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise DesignerError("Could not reach the Anthropic API.") from exc
    # RateLimitError / 5xx are already retried by the SDK; if they still fail they propagate

    if response.stop_reason == "refusal":
        raise DesignerError("The model declined to design scenarios for this page.")
    if response.stop_reason == "max_tokens":
        raise DesignerError(f"The Designer response was cut off at {MAX_TOKENS} tokens.")
    parsed = response.parsed_output
    if parsed is None:
        raise DesignerError("The Designer response did not match the scenario schema.")

    usage = response.usage
    return DesignResult(
        scenarios=parsed.scenarios,
        model=response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
    )
