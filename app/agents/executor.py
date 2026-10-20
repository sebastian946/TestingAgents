"""The Executor (WTA-33, ADR-10): runs a designed scenario in a real browser and judges it.

For each scenario a Claude model (Sonnet, `EXECUTOR_MODEL`) drives Chromium through a small
set of tools (navigate, click, fill, press, read_page, finish) and decides whether the
expected result was met: `pass`, `fail`, or `blocked` when it could not get far enough.
The tools are plain Playwright calls exposed with the Messages API tool-use loop; the same
contract an MCP server would offer, without a second process.

Guardrails, because the agent acts as a real user on the customer's site:
- the browser stays on the job's host (navigations elsewhere are refused);
- clicks and fills on anything that looks like a destructive or paid action (pay, delete,
  purchase, unsubscribe... `UNSAFE_LINK_WORDS`) are refused, whatever the scenario says;
- a step cap per scenario (`EXECUTOR_MAX_STEPS`) and a time cap; past either, `blocked`;
- the page is read as text and an inventory, never as a screenshot sent to the model
  (cheaper, and no customer pixels leave the server); a screenshot is saved locally as
  evidence of the final state.
The run only happens on a test environment the user confirmed (`RunCreate.test_environment`).
"""
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import anthropic

from agents.designer import CallUsage, _default_client, model_profile
from config.variables import settings

MAX_TOKENS = 1024
PAGE_TEXT_LIMIT = 4000
INVENTORY_LIMIT = 40

SYSTEM_PROMPT = """You are a careful QA engineer executing ONE manual test scenario in a real browser.

You get the scenario (steps and expected result) and tools that act on the browser. Work step
by step: read the page before acting, perform each step, then compare what you see with the
expected result and call `finish` exactly once with the outcome:
- "pass": the expected result was observed;
- "fail": the steps worked but the result differs (describe exactly what you saw);
- "blocked": you could not perform the steps (element not found, site error, refused action).

Rules:
- Never attempt payments, purchases, deletions, cancellations or anything irreversible, even
  if a step says so: finish as "blocked" and explain. The tools refuse such actions too.
- Stay on the site under test. Do not log in with any credentials other than the ones the
  tools already applied.
- Placeholders in <angle brackets> (e.g. <valid email>) mean you should invent realistic
  test data for them; never real personal data.
- Be concise in the reason: one or two sentences a tester can verify.
- Do not loop: if an action fails twice, finish as "blocked"."""

TOOLS: list[dict[str, Any]] = [
    {"name": "navigate", "description": "Open a URL on the site under test.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "read_page", "description": "Current URL, title, visible text (truncated) and the interactive elements: inputs, buttons, links.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "click", "description": "Click a button or link. `target` is its visible text, label or a CSS selector.",
     "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}},
    {"name": "fill", "description": "Type into an input or textarea. `target` is its label, placeholder, name or a CSS selector.",
     "input_schema": {"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}},
                      "required": ["target", "text"]}},
    {"name": "press", "description": "Press a keyboard key on the focused element (e.g. Enter, Tab, Escape).",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "finish", "description": "Report the outcome of the scenario. Call it exactly once, at the end.",
     "input_schema": {"type": "object", "properties": {
         "outcome": {"type": "string", "enum": ["pass", "fail", "blocked"]},
         "reason": {"type": "string", "description": "What was observed, in one or two sentences"}},
         "required": ["outcome", "reason"]}},
]

INVENTORY_JS = """() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const labelOf = (el) => { if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return txt(l); }
    const p = el.closest('label'); return p ? txt(p) : ''; };
  const inputs = [...document.querySelectorAll('input:not([type=hidden]), textarea, select')].filter(vis).slice(0, %d)
    .map(el => ({tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', label: labelOf(el),
                 placeholder: el.placeholder || '', value: el.type === 'password' ? (el.value ? '***' : '') : (el.value || '').slice(0, 40)}));
  const buttons = [...document.querySelectorAll('button, input[type=submit], input[type=button], [role=button]')].filter(vis).slice(0, %d)
    .map(el => ({text: txt(el)}));
  const links = [...document.querySelectorAll('a[href]')].filter(vis).slice(0, %d)
    .map(el => ({text: txt(el), href: el.href}));
  const alerts = [...document.querySelectorAll('[role=alert], .error, .alert, .invalid-feedback, [aria-invalid=true]')].filter(vis).slice(0, 10)
    .map(el => txt(el)).filter(Boolean);
  return {inputs, buttons, links, alerts, text: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, %d)};
}""" % (INVENTORY_LIMIT, INVENTORY_LIMIT, INVENTORY_LIMIT, PAGE_TEXT_LIMIT)


# Actions the Executor never performs, however the scenario reads them. Narrower than the
# crawler's UNSAFE_LINK_WORDS on purpose: submitting a contact form ("send", "submit",
# "confirm") is exactly what a scenario tests; paying, buying or deleting never is
FORBIDDEN_WORDS = frozenset({
    "pay", "payment", "purchase", "buy", "checkout", "delete", "remove", "destroy", "cancel", "unsubscribe",
    "deactivate", "transfer", "withdraw", "revoke", "disable", "erase", "wipe",
})


def _forbidden(text: str) -> bool:
    """True when any word of a URL path, a label or a link text is a forbidden action."""
    words = re.split(r"[^a-z]+", text.lower())
    joined = {w for w in words if w} | {a + b for a, b in zip(words, words[1:])}
    return bool(joined & FORBIDDEN_WORDS)


class RefusedAction(Exception):
    """An action the guardrails do not allow; reported to the model, never raised outward."""


@dataclass
class StepLog:
    tool: str
    input: dict[str, Any]
    result: str


@dataclass
class ScenarioResult:
    outcome: str  # pass | fail | blocked
    reason: str
    steps: list[StepLog] = field(default_factory=list)
    screenshot_path: str | None = None
    calls: int = 0


@dataclass
class ScenarioToRun:
    scenario_id: int
    code: str
    title: str
    steps: list[str]
    expected_result: str
    page_url: str
    page_id: int | None = None


# ----------------------------------------------------------------- browser tools

class BrowserTools:
    """The tools, bound to one Playwright page and one host."""

    def __init__(self, page, host: str, timeout_ms: int = 10_000):
        self.page = page
        self.host = host
        self.timeout_ms = timeout_ms

    def _same_site(self, url: str) -> bool:
        return (urlsplit(url).hostname or "").lower() == self.host.lower()

    def navigate(self, url: str) -> str:
        if not self._same_site(url):
            raise RefusedAction(f"Refused: {url} is not on the site under test ({self.host}).")
        if _forbidden(urlsplit(url).path):
            raise RefusedAction("Refused: that URL looks like a destructive or paid action.")
        self.page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
        self._settle()
        return f"Opened {self.page.url}"

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            pass

    def _locate(self, target: str, kind: str):
        """Resolve a human target (text, label, placeholder, name) or a CSS selector to one
        visible element. Tries the accessible ways first, CSS last."""
        page = self.page
        candidates = []
        if kind == "fill":
            candidates += [page.get_by_label(target, exact=False), page.get_by_placeholder(target, exact=False),
                           page.locator(f"[name='{target}']"), page.locator(f"#{target}")]
        else:
            candidates += [page.get_by_role("button", name=target, exact=False), page.get_by_role("link", name=target, exact=False),
                           page.get_by_text(target, exact=False), page.locator(f"[name='{target}']"), page.locator(f"#{target}")]
        if any(ch in target for ch in "#.[]>:"):
            candidates.append(page.locator(target))
        for locator in candidates:
            try:
                visible = locator.locator("visible=true")
                if visible.count() > 0:
                    return visible.first
            except Exception:
                continue
        raise RefusedAction(f"Element not found: {target!r}. Call read_page to see what is on the page.")

    def _guard(self, target: str, element) -> None:
        text = target
        try:
            text += " " + (element.inner_text(timeout=1000) or "")
            text += " " + (element.get_attribute("href") or "")
        except Exception:
            pass
        if _forbidden(text):
            raise RefusedAction("Refused: that element looks like a destructive or paid action (pay, delete, cancel...).")

    def click(self, target: str) -> str:
        element = self._locate(target, "click")
        self._guard(target, element)
        element.click(timeout=self.timeout_ms)
        self._settle()
        return f"Clicked {target!r}. Now at {self.page.url}"

    def fill(self, target: str, text: str) -> str:
        element = self._locate(target, "fill")
        element.fill(text, timeout=self.timeout_ms)
        return f"Filled {target!r}"

    def press(self, key: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9+]{1,20}", key):
            raise RefusedAction("Refused: unknown key.")
        self.page.keyboard.press(key)
        self._settle()
        return f"Pressed {key}. Now at {self.page.url}"

    def read_page(self) -> str:
        data = self.page.evaluate(INVENTORY_JS)
        data = {"url": self.page.url, "title": self.page.title(), **data}
        return json.dumps(data, ensure_ascii=False)

    def screenshot(self, path: Path) -> str | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path), full_page=False, timeout=10_000)
            return str(path)
        except Exception:
            return None


# ----------------------------------------------------------------- the loop

def _scenario_message(s: ScenarioToRun) -> str:
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(s.steps, start=1))
    return (f"<scenario>\n<id>{s.code}</id>\n<title>{s.title}</title>\n<page>{s.page_url}</page>\n"
            f"<steps>\n{steps}\n</steps>\n<expected_result>{s.expected_result}</expected_result>\n</scenario>\n\n"
            "The browser is already on the page. Execute the steps and finish with the outcome.")


def _usage_of(response, requested: str, attempt: int) -> CallUsage:
    u = response.usage
    return CallUsage(
        requested_model=requested, model=getattr(response, "model", requested) or requested, attempt=attempt,
        outcome="ok", input_tokens=u.input_tokens, output_tokens=u.output_tokens,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def execute_scenario(
    scenario: ScenarioToRun,
    tools: BrowserTools,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
    max_steps: int | None = None,
    time_budget: float | None = None,
    on_call: Callable[[CallUsage], None] | None = None,
    screenshot_path: Path | None = None,
) -> ScenarioResult:
    """Drive one scenario to a verdict. Never raises for a scenario-level problem: a tool
    error becomes a `blocked` result; API errors propagate (the run marks itself failed)."""
    model = model or settings.executor_model
    max_steps = max_steps or settings.executor_max_steps
    time_budget = time_budget or settings.executor_scenario_timeout
    result = ScenarioResult(outcome="blocked", reason="The scenario did not finish.")
    started = time.monotonic()

    try:
        tools.navigate(scenario.page_url)
    except RefusedAction as exc:
        result.reason = str(exc)
        return result
    except Exception as exc:  # the page itself is down
        result.reason = f"Could not open {scenario.page_url}: {exc.__class__.__name__}"
        return result

    if settings.designer_mode == "fake":
        return _fake_execute(scenario, tools, result, screenshot_path)

    client = client or _default_client()
    profile = model_profile(model)
    messages: list[dict[str, Any]] = [{"role": "user", "content": _scenario_message(scenario)}]
    kwargs: dict[str, Any] = {"model": model, "max_tokens": MAX_TOKENS, "tools": TOOLS,
                              "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]}
    if profile.supports_effort:
        kwargs["output_config"] = {"effort": settings.executor_effort}

    for attempt in range(1, max_steps + 1):
        if time.monotonic() - started > time_budget:
            result.reason = f"Time limit of {int(time_budget)}s reached after {attempt - 1} steps."
            break
        response = client.messages.create(messages=messages, **kwargs)
        result.calls += 1
        if on_call is not None:
            on_call(_usage_of(response, model, attempt))
        tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if not tool_uses:
            # The model answered in prose: treat it as a verdict only if it is explicit
            text = " ".join(getattr(b, "text", "") for b in response.content).strip()
            result.reason = text[:500] or "The model stopped without calling finish."
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        finished = False
        for use in tool_uses:
            name, args = use.name, dict(use.input or {})
            if name == "finish":
                outcome = str(args.get("outcome", "blocked"))
                result.outcome = outcome if outcome in ("pass", "fail", "blocked") else "blocked"
                result.reason = str(args.get("reason", ""))[:1000] or "No reason given."
                result.steps.append(StepLog("finish", args, result.outcome))
                finished = True
                break
            output = _run_tool(tools, name, args)
            result.steps.append(StepLog(name, args, output[:300]))
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": output})
        if finished:
            break
        messages.append({"role": "user", "content": results})
    else:
        result.reason = f"Step limit of {max_steps} reached without a verdict."

    if screenshot_path is not None:
        result.screenshot_path = tools.screenshot(screenshot_path)
    return result


def _run_tool(tools: BrowserTools, name: str, args: dict[str, Any]) -> str:
    try:
        if name == "navigate":
            return tools.navigate(str(args.get("url", "")))
        if name == "read_page":
            return tools.read_page()
        if name == "click":
            return tools.click(str(args.get("target", "")))
        if name == "fill":
            return tools.fill(str(args.get("target", "")), str(args.get("text", "")))
        if name == "press":
            return tools.press(str(args.get("key", "")))
        return f"Unknown tool {name}"
    except RefusedAction as exc:
        return str(exc)
    except Exception as exc:  # Playwright timeouts and the like: the model decides what next
        return f"Error: {exc.__class__.__name__}: {str(exc).splitlines()[0][:200]}"


def _fake_execute(scenario: ScenarioToRun, tools: BrowserTools, result: ScenarioResult, shot: Path | None) -> ScenarioResult:
    """DESIGNER_MODE=fake: no model. Pass when the page has a title, so the whole run
    pipeline can be exercised by tests without an API key."""
    inventory = json.loads(tools.read_page())
    result.steps.append(StepLog("read_page", {}, f"{len(inventory['inputs'])} inputs, {len(inventory['buttons'])} buttons"))
    if inventory.get("title"):
        result.outcome, result.reason = "pass", f"The page '{inventory['title']}' loaded with its content (fake executor)."
    else:
        result.outcome, result.reason = "fail", "The page has no title (fake executor)."
    result.steps.append(StepLog("finish", {"outcome": result.outcome}, result.outcome))
    if shot is not None:
        result.screenshot_path = tools.screenshot(shot)
    return result
