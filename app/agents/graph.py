"""The job pipeline as a LangGraph StateGraph (WTA-21).

    START -> explorer --(pages?)--> designer --(scenarios?)--> documenter -> END
                 \\ no pages              \\ no scenarios
                  `--------------------> fail ------------------------> END

- **explorer**: crawls the site (BFS, Playwright), saves each page as it is found.
- **designer**: one Claude call per page, saves each page's scenarios and every LLM call.
- **documenter**: writes the Excel report and marks the job `done`.
- **fail**: marks the job `failed` with a readable reason.

The two conditional edges make "nothing to work with" a normal path of the graph that
ends in `failed`, not an exception. Exceptions are kept for real errors (the browser
crashed, the API key is invalid, the database is down); the worker catches those.

Each node reads only the state keys it needs and returns only the keys it owns (see
`agents.state`). Collaborators (`crawl`, `design_pages`, `generate_report`,
`local_session`) are module-level names so tests can replace them.
"""
import shutil
import uuid
from pathlib import Path
from typing import Literal

from langgraph.graph import END, START, StateGraph

from agents.designer import CallUsage, PageDesign, PageLike, design_pages
from agents.documenter import generate_report, report_file
from agents.explorer import DEFAULT_MAX_PAGES, CrawledPage, LoginResult, crawl
from agents.state import PageGap, PageState, TestingState
from config.variables import settings
from db import db_crud
from db.conn import local_session
from security import credentials

NO_PAGES_ERROR = "No page could be fetched from the given URL."


class _Page:
    """Attribute view of a PageState, the shape `design_pages` expects (url, title, ...)."""

    def __init__(self, data: PageState):
        self.page_id = data["page_id"]
        self.url = data["url"]
        self.title = data["title"]
        self.page_type = data["page_type"]
        self.elements = data["elements"]


# ----------------------------------------------------------------------------- nodes

def explorer(state: TestingState) -> dict:
    """Reads job_id, url. Writes pages, login_note."""
    job_id = state["job_id"]
    job_uuid = uuid.UUID(job_id)
    found: list[PageState] = []
    login_note: str | None = None

    # WTA-29: the credentials never enter the state (which is logged and traced); they are
    # read from the row here, used by the browser, and forgotten right after the crawl
    login = None
    with local_session() as db:
        job = db_crud.get_job(db, job_uuid)
        blob = job.login_credentials if job else None
    if blob:
        try:
            login = credentials.decrypt(blob)
        except credentials.CredentialsError as exc:
            login_note = f"Login skipped: {exc}"

    def note_login(outcome: LoginResult) -> None:
        nonlocal login_note
        login_note = None if outcome.ok else outcome.message

    def persist_page(page: CrawledPage) -> None:
        # One short transaction per page (WTA-14): the row and the pages_crawled increment
        # are committed together, so GET /jobs/{id} (another process) sees the counter grow
        # live and it always equals the number of rows. If the worker dies mid-crawl, the
        # pages already committed stay; a single commit at the end would lose all of them.
        with local_session() as db:
            row = db_crud.create_page(
                db,
                job_uuid,
                url=page.url,
                title=page.title,
                elements=page.elements,
                page_type=page.page_type,
                screenshot_path=page.screenshot_path,
            )
            found.append(PageState(
                page_id=row.id, url=page.url, title=page.title, page_type=page.page_type, elements=page.elements,
            ))

    # Per-job folder: reports/<job_id>/screenshots/<page>.png (WTA-12). mark_job_running
    # wiped the previous attempt's rows; drop its files too
    screenshot_dir = Path(settings.reports_dir) / job_id / "screenshots"
    shutil.rmtree(screenshot_dir, ignore_errors=True)
    report_file(settings.reports_dir, job_id).unlink(missing_ok=True)

    # use_browser: render JavaScript and extract the PageInfo inventory (WTA-11). When the
    # user will pick the pages (WTA-32) the crawl goes wider: it costs time, not tokens
    max_pages = settings.discovery_max_pages if state.get("select_pages") else DEFAULT_MAX_PAGES
    try:
        crawl(state["url"], max_pages=max_pages, on_page=persist_page, use_browser=True,
              screenshot_dir=screenshot_dir, login=login, on_login=note_login)
    finally:
        if blob:  # the browser is closed: nothing needs the secret any more, whatever happened
            with local_session() as db:
                db_crud.clear_job_credentials(db, job_uuid)
    print(f"[graph] explorer: {len(found)} pages")
    return {"pages": found, "login_note": login_note}


def designer(state: TestingState) -> dict:
    """Reads job_id, description, language, pages. Writes scenarios_designed, pages_without_scenarios."""
    job_uuid = uuid.UUID(state["job_id"])

    def persist_scenarios(outcome: PageDesign) -> None:
        # Same pattern as the pages (WTA-17): one short transaction per page, rows and
        # total_scenarios increment together, so progress is visible live
        if outcome.result is None:
            return
        page: _Page = outcome.page  # type: ignore[assignment]
        with local_session() as db:
            db_crud.create_scenarios(
                db, job_uuid, page.page_id, [s.model_dump() for s in outcome.result.scenarios],
            )
        r = outcome.result
        print(f"[designer] {page.url}: {len(r.scenarios)} scenarios "
              f"(attempts={r.attempts}, ~US${r.estimated_cost_usd:.4f}, {r.model})")

    def log_llm_call(page: PageLike, call: CallUsage) -> None:
        # Every billed call, failed ones included, in its own short transaction (WTA-18)
        with local_session() as db:
            db_crud.create_llm_call(
                db,
                job_uuid,
                page_id=getattr(page, "page_id", None),
                agent="designer",
                model=call.model,
                attempt=call.attempt,
                outcome=call.outcome,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cache_read_input_tokens=call.cache_read_input_tokens,
                cache_creation_input_tokens=call.cache_creation_input_tokens,
                cost_usd=call.cost_usd,
            )

    # A page that fails is skipped; DesignerFatalError (bad key, unknown model) is raised
    pages = [_Page(p) for p in state.get("pages", [])]
    outcomes = design_pages(
        pages, state["description"], on_result=persist_scenarios, on_call=log_llm_call,
        language=state.get("language", "en"),
    )
    designed = sum(len(o.result.scenarios) for o in outcomes if o.result)
    gaps = [PageGap(url=o.page.url, error=o.error or "unknown") for o in outcomes if o.result is None]
    print(f"[graph] designer: {designed} scenarios, {len(gaps)} pages without scenarios")
    return {"scenarios_designed": designed, "pages_without_scenarios": gaps}


def documenter(state: TestingState) -> dict:
    """Reads job_id, pages, pages_without_scenarios. Writes report_path, status, error."""
    job_uuid = uuid.UUID(state["job_id"])
    gaps = state.get("pages_without_scenarios", [])
    notes = []
    if state.get("login_note"):  # WTA-29: a failed login is a gap the customer must know about
        notes.append(f"{state['login_note']} The site was explored as an anonymous visitor.")
    if gaps:
        notes.append(f"No scenarios for {len(gaps)} of {len(state.get('pages', []))} pages: " + "; ".join(
            f"{g['url']} ({g['error']})" for g in gaps
        ))
    summary = " ".join(notes) or None
    # The report is built from what is in the database (WTA-19). Only then is the job DONE,
    # so a done job always has its report
    with local_session() as db:
        path = generate_report(db, job_uuid, settings.reports_dir)
        db_crud.mark_job_done(db, job_uuid, report_path=str(path), error=summary)
    print(f"[graph] documenter: {path}")
    return {"report_path": str(path), "status": "done", "error": summary}


def pause(state: TestingState) -> dict:
    """Reads job_id, pages. Writes status. WTA-32: the job stops here until the user selects
    the pages; `continue_job_task` then runs `continuation_graph` on them."""
    with local_session() as db:
        db_crud.mark_job_awaiting_selection(db, uuid.UUID(state["job_id"]))
    print(f"[graph] pause: {len(state.get('pages', []))} pages found, waiting for the selection")
    return {"status": "awaiting_selection"}


def fail(state: TestingState) -> dict:
    """Reads pages, scenarios_designed, pages_without_scenarios. Writes status, error."""
    if not state.get("pages"):
        reason = NO_PAGES_ERROR
    else:
        gaps = state.get("pages_without_scenarios") or []
        first = f" First error: {gaps[0]['error']}" if gaps else ""
        reason = f"No scenarios could be designed for any page.{first}"
    with local_session() as db:
        db_crud.mark_job_failed(db, uuid.UUID(state["job_id"]), reason)
    print(f"[graph] fail: {reason}")
    return {"status": "failed", "error": reason}


# ----------------------------------------------------------------------- conditional edges

def route_after_explorer(state: TestingState) -> Literal["designer", "pause", "fail"]:
    """Pages found -> design them (or pause for the user's selection, WTA-32); none -> fail."""
    if not state.get("pages"):
        return "fail"
    return "pause" if state.get("select_pages") else "designer"


def route_after_designer(state: TestingState) -> Literal["documenter", "fail"]:
    """At least one scenario -> write the report; none at all -> fail."""
    return "documenter" if state.get("scenarios_designed", 0) > 0 else "fail"


# ----------------------------------------------------------------------------- graph

def build_graph() -> StateGraph:
    graph = StateGraph(TestingState)
    graph.add_node("explorer", explorer)
    graph.add_node("designer", designer)
    graph.add_node("documenter", documenter)
    graph.add_node("pause", pause)
    graph.add_node("fail", fail)

    graph.add_edge(START, "explorer")
    graph.add_conditional_edges("explorer", route_after_explorer, {"designer": "designer", "pause": "pause", "fail": "fail"})
    graph.add_conditional_edges("designer", route_after_designer, {"documenter": "documenter", "fail": "fail"})
    graph.add_edge("documenter", END)
    graph.add_edge("pause", END)
    graph.add_edge("fail", END)
    return graph


testing_graph = build_graph().compile()


def build_continuation_graph() -> StateGraph:
    """WTA-32: the second half of a paused job. The worker loads the selected pages into the
    state (`continue_job_task`) and the designer and documenter run exactly as in the main
    graph: same nodes, same ownership of state keys."""
    graph = StateGraph(TestingState)
    graph.add_node("designer", designer)
    graph.add_node("documenter", documenter)
    graph.add_node("fail", fail)
    graph.add_edge(START, "designer")
    graph.add_conditional_edges("designer", route_after_designer, {"documenter": "documenter", "fail": "fail"})
    graph.add_edge("documenter", END)
    graph.add_edge("fail", END)
    return graph


continuation_graph = build_continuation_graph().compile()
