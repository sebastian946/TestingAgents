"""Shared state of the LangGraph pipeline (WTA-21).

The state is what travels between nodes. Each node receives the whole state, reads only the
keys it needs and returns a dict with only the keys it owns; LangGraph merges that update
into the state before the next node runs. Ownership:

| Key                       | Written by         | Read by                         |
|---------------------------|--------------------|---------------------------------|
| job_id, url, description, language | the worker (input) | explorer, designer, documenter |
| pages                     | explorer           | route_after_explorer, designer, fail |
| scenarios_designed        | designer           | route_after_designer, fail      |
| pages_without_scenarios   | designer           | documenter, fail                |
| report_path               | documenter         | the worker (output)             |
| status, error             | documenter or fail | the worker (output)             |

Everything is plain JSON-able data (ids, strings, dicts), never ORM objects, DB sessions
or the browser: those live inside a node and die with it. That keeps the state small and
loggable, and lets a checkpointer persist it later without changes.

Heavy results are not duplicated here: pages and scenarios are committed to Postgres as
they are produced (live progress, WTA-14/17), so the state carries only what the next node
needs, plus counters.
"""
from typing import Any, Literal, NotRequired, TypedDict


class PageState(TypedDict):
    """A crawled page as the Designer needs it."""

    page_id: int  # pages.id, to link its scenarios and LLM calls
    url: str
    title: str | None
    page_type: str | None
    elements: dict[str, Any] | None  # PageInfo inventory (WTA-11)


class PageGap(TypedDict):
    """A page the Designer could not cover, and why."""

    url: str
    error: str


class TestingState(TypedDict):
    # Input, set by the worker before graph.invoke
    job_id: str
    url: str
    description: str | None
    language: NotRequired[str]  # "en" | "es" (config/languages.py); "en" when missing

    # explorer
    pages: NotRequired[list[PageState]]

    # designer
    scenarios_designed: NotRequired[int]
    pages_without_scenarios: NotRequired[list[PageGap]]

    # documenter
    report_path: NotRequired[str]

    # terminal nodes (documenter on success, fail otherwise)
    status: NotRequired[Literal["done", "failed"]]
    error: NotRequired[str | None]
