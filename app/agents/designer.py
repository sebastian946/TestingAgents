"""Designer agent: turns a crawled page into test scenarios with Claude (WTA-15, WTA-16).

One API call per page (two if the first response fails validation). The prompt lives in
versioned files (``agents/prompts/designer/vN``), never in code; the response is constrained
to the ``DesignerOutput`` JSON schema with structured outputs and validated by Pydantic.

Reliability (WTA-16):
- Self-healing output: if the response does not validate (broken JSON, empty steps,
  duplicate titles...), the request is sent once more with the previous response and the
  validation errors as feedback. A second failure gives up on that page.
- Transient API errors (429 rate limit, 529 overloaded, 5xx, timeouts, connection drops)
  are retried by the SDK with exponential backoff that honors ``retry-after``
  (``DESIGNER_MAX_RETRIES``, ``DESIGNER_TIMEOUT``); we do not re-implement that.
- Errors are split in two: ``DesignerError`` means *this page* has no scenarios and the job
  goes on with the next page (``design_pages``); ``DesignerFatalError`` (bad API key,
  unknown model, invalid request) would fail every page the same way, so it stops the run.

API choices:
- Model, effort, prompt version, retries and timeout come from settings (``DESIGNER_*``).
  The request only sends what the chosen model accepts (see ``MODEL_PROFILES``), so the
  model can be switched from .env without a code change. The choice is recorded in
  docs/adr/ADR-02-designer-model.md.
- No ``temperature``: current Claude models reject sampling parameters.
- The system prompt is static and marked for prompt caching; everything that changes per
  page goes in the user message.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any, Callable, Iterable, Protocol

import anthropic
from pydantic import ValidationError

from config.variables import settings
from models.agent_models import DesignerOutput, TestScenario

PROMPTS_DIR = Path(__file__).parent / "prompts" / "designer"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
MAX_ATTEMPTS = 2  # first try + one self-healing retry (WTA-16)
NO_DESCRIPTION = "(not provided)"
# Counters used by the page classifier: noise for the LLM, and they cost tokens
INVENTORY_DROP_KEYS = {"signals", "url", "title"}
MAX_FEEDBACK_RESPONSE_CHARS = 20_000  # cap on the invalid response echoed back as feedback

OUTPUT_FORMAT = {"type": "json_schema", "schema": anthropic.transform_schema(DesignerOutput.model_json_schema())}


@dataclass(frozen=True)
class ModelProfile:
    supports_effort: bool  # output_config.effort (Haiku 4.5 rejects it)
    supports_fallbacks: bool  # server-side refusal fallback
    input_usd_per_mtok: float  # list prices, Claude API, 2026-09
    output_usd_per_mtok: float


MODEL_PROFILES: dict[str, ModelProfile] = {
    "claude-opus-5-5": ModelProfile(True, True, 4.0, 20.0),
    "claude-sonnet-5-5": ModelProfile(True, True, 2.0, 10.0),
    "claude-haiku-4-5": ModelProfile(False, False, 1.0, 5.0),
}
# Unknown model ids get the conservative profile: no optional fields, no cost estimate
UNKNOWN_MODEL = ModelProfile(False, False, 0.0, 0.0)


def model_profile(model: str) -> ModelProfile:
    return MODEL_PROFILES.get(model, UNKNOWN_MODEL)


class DesignerError(RuntimeError):
    """This page could not get scenarios; the job should continue. The message is safe to store."""


class DesignerFatalError(DesignerError):
    """Configuration problem that would fail every page the same way: stop the run."""


class PageLike(Protocol):
    url: str
    title: str | None
    page_type: str | None
    elements: dict[str, Any] | None


@dataclass
class DesignResult:
    scenarios: list[TestScenario]
    model: str  # model that actually served the last response (differs if a fallback ran)
    attempts: int  # 1, or 2 when the self-healing retry was needed
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int

    @property
    def estimated_cost_usd(self) -> float:
        """List-price estimate; cache writes cost 1.25x input, cache reads 0.1x."""
        p = model_profile(self.model)
        tokens_in = self.input_tokens + 1.25 * self.cache_creation_input_tokens + 0.1 * self.cache_read_input_tokens
        return (tokens_in * p.input_usd_per_mtok + self.output_tokens * p.output_usd_per_mtok) / 1_000_000


@dataclass
class PageDesign:
    """Outcome of one page in `design_pages`: scenarios, or the reason it has none."""

    page: PageLike
    result: DesignResult | None = None
    error: str | None = None


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_input_tokens += usage.cache_read_input_tokens or 0
        self.cache_creation_input_tokens += usage.cache_creation_input_tokens or 0


# --------------------------------------------------------------------------------- prompt

@lru_cache
def load_prompt(version: str) -> tuple[str, Template]:
    """Return (system prompt, user message template) for a prompt version."""
    folder = PROMPTS_DIR / version
    if not folder.is_dir():
        available = sorted(p.name for p in PROMPTS_DIR.iterdir() if p.is_dir())
        raise DesignerFatalError(f"Unknown designer prompt version {version!r}; available: {available}")
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


def feedback_message(user: str, invalid_response: str, error: ValidationError) -> str:
    """User message for the self-healing retry: original request + what was wrong.

    A fresh single-turn request (rather than replaying the assistant turn) keeps the retry
    independent of the first call's thinking blocks.
    """
    problems = "\n".join(
        f"- {'.'.join(str(p) for p in e['loc']) or '(response)'}: {e['msg']}" for e in error.errors(include_url=False)
    )
    return (
        f"{user}\n\n"
        "A previous attempt at this task returned a response that failed validation.\n"
        f"<previous_response>\n{invalid_response[:MAX_FEEDBACK_RESPONSE_CHARS]}\n</previous_response>\n"
        f"<validation_errors>\n{problems}\n</validation_errors>\n"
        "Return the complete corrected response for this page. Fix what the errors describe and "
        "keep the scenarios that were already valid."
    )


# ------------------------------------------------------------------------------- API call

def _default_client() -> anthropic.Anthropic:
    options = {"max_retries": settings.designer_max_retries, "timeout": settings.designer_timeout}
    # With no key in .env the SDK still resolves ANTHROPIC_API_KEY from the environment
    # or an `ant auth login` profile
    if settings.anthropic_api_key:
        return anthropic.Anthropic(api_key=settings.anthropic_api_key, **options)
    return anthropic.Anthropic(**options)


def _request(client: anthropic.Anthropic, model: str, effort: str, system: str, user: str):
    profile = model_profile(model)
    output_config: dict[str, Any] = {"format": OUTPUT_FORMAT}
    if profile.supports_effort:
        output_config["effort"] = effort
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": output_config,
    }
    if profile.supports_fallbacks:
        kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
    try:
        return client.beta.messages.create(**kwargs)
    # Configuration errors: every page would fail the same way
    except anthropic.AuthenticationError as exc:
        raise DesignerFatalError("The Anthropic API key is missing or invalid (ANTHROPIC_API_KEY in .env).") from exc
    except anthropic.PermissionDeniedError as exc:
        raise DesignerFatalError(f"The API key cannot use model {model!r}.") from exc
    except anthropic.NotFoundError as exc:
        raise DesignerFatalError(f"Unknown model {model!r} (DESIGNER_MODEL in .env).") from exc
    except (anthropic.BadRequestError, anthropic.UnprocessableEntityError) as exc:
        raise DesignerFatalError(f"The Designer request was rejected: {exc.message}") from exc
    # Page-level errors: the SDK already retried the transient ones with backoff
    except anthropic.RequestTooLargeError as exc:
        raise DesignerError("The page inventory is too large for one request.") from exc
    except (anthropic.RateLimitError, anthropic.OverloadedError, anthropic.ServiceUnavailableError) as exc:
        raise DesignerError(f"The Anthropic API is busy ({exc.status_code}); gave up after retries.") from exc
    except anthropic.APIStatusError as exc:
        raise DesignerError(f"The Anthropic API failed ({exc.status_code}); gave up after retries.") from exc
    except anthropic.APITimeoutError as exc:
        raise DesignerError(f"The Designer call timed out after {settings.designer_timeout:.0f}s.") from exc
    except anthropic.APIConnectionError as exc:
        raise DesignerError("Could not reach the Anthropic API.") from exc


def _response_text(response: Any) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


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
    Raises `DesignerError` if this page cannot get scenarios, `DesignerFatalError` if no page can.
    """
    system, user = render_prompt(page, description, version or settings.designer_prompt_version)
    client = client or _default_client()
    model = model or settings.designer_model
    effort = effort or settings.designer_effort
    usage = _Usage()

    message = user
    last_error: ValidationError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = _request(client, model, effort, system, message)
        usage.add(response.usage)

        if response.stop_reason == "refusal":
            raise DesignerError("The model declined to design scenarios for this page.")
        if response.stop_reason == "max_tokens":
            raise DesignerError(f"The Designer response was cut off at {MAX_TOKENS} tokens.")

        text = _response_text(response)
        try:
            parsed = DesignerOutput.model_validate_json(text)
        except ValidationError as exc:
            last_error = exc
            print(f"[designer] attempt {attempt} for {page.url} failed validation ({exc.error_count()} errors)")
            message = feedback_message(user, text, exc)
            continue
        return DesignResult(
            scenarios=parsed.scenarios,
            model=response.model,
            attempts=attempt,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
        )

    assert last_error is not None
    first = last_error.errors(include_url=False)[0]
    raise DesignerError(
        f"The Designer response failed validation after {MAX_ATTEMPTS} attempts "
        f"({last_error.error_count()} errors, first: {first['msg']})."
    )


def design_pages(
    pages: Iterable[PageLike],
    description: str | None = None,
    *,
    client: anthropic.Anthropic | None = None,
    on_result: Callable[[PageDesign], None] | None = None,
    **options: Any,
) -> list[PageDesign]:
    """Design every page; a page that fails is recorded and the rest continue (WTA-16).

    Returns one `PageDesign` per page, in order. `on_result` is called right after each page
    so the caller can persist it immediately (WTA-17), like the explorer's `on_page`. Only
    `DesignerFatalError` (bad key, unknown model...) propagates, since retrying it on the
    remaining pages would only repeat it.
    """
    client = client or _default_client()  # one client: connection reuse across pages
    outcomes: list[PageDesign] = []
    for page in pages:
        try:
            outcome = PageDesign(page, result=design_scenarios(page, description, client=client, **options))
        except DesignerFatalError:
            raise
        except DesignerError as exc:
            print(f"[designer] no scenarios for {page.url}: {exc}")
            outcome = PageDesign(page, error=str(exc))
        outcomes.append(outcome)
        if on_result is not None:
            on_result(outcome)
    return outcomes
