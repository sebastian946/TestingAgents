"""The test-run task (WTA-33): open Chromium once, log in if asked, execute every scenario
of the job's selected pages with the Executor, persist each verdict as it comes, then
rebuild the Excel so it carries the Results sheet.

Like the other tasks it keeps sessions short (one commit per scenario), never holds a DB
connection while the browser works, and forgets the login credentials when it ends.
"""
import uuid
from pathlib import Path

from agents.designer import CallUsage
from agents.documenter import generate_report
from agents.executor import BrowserTools, ScenarioToRun, execute_scenario
from agents.explorer import PlaywrightFetcher
from config.variables import settings
from db import db_crud
from db.conn import local_session
from security import credentials
from urllib.parse import urlsplit


def run_tests(run_id: int) -> dict:
    with local_session() as db:
        run = db_crud.get_run(db, run_id)
        if run is None:
            print(f"Run {run_id} not found, skipping.")
            return {"status": "missing"}
        job = db_crud.get_job(db, run.job_id)
        selected = {p.id: p for p in db_crud.get_selected_pages(db, run.job_id)}
        scenarios = [s for s in db_crud.get_scenarios_by_job(db, run.job_id) if s.page_id in selected]
        todo = [ScenarioToRun(s.id, s.scenario_code, s.title, [str(x) for x in (s.steps or [])], s.expected_result,
                              selected[s.page_id].url, s.page_id,
                              logged_out=bool(run.login_credentials) and selected[s.page_id].page_type == "login")
                for s in scenarios][: settings.executor_max_scenarios]
        job_id, job_url = job.id, job.url
        blob = run.login_credentials
        db_crud.mark_run_running(db, run_id, total=len(todo))
    print(f"[run {run_id}] {len(todo)} scenarios of job {job_id}")

    login = credentials.decrypt(blob) if blob else None
    host = urlsplit(job_url).hostname or ""
    shots_dir = Path(settings.reports_dir) / str(job_id) / "runs" / str(run_id)
    error = None
    try:
        with PlaywrightFetcher() as browser:
            if login is not None:
                outcome = browser.login(login, timeout=15)
                print(f"[run {run_id}] {outcome.message}")
                if not outcome.ok:
                    error = f"{outcome.message} The scenarios were not run."
            if error is None:
                page = browser.new_page()
                # The run only happens on a declared test environment, so purchases are allowed
                tools = BrowserTools(page, host, login=login, fetcher=browser, allow_purchases=True)
                signed_in = login is not None
                for s in todo:
                    # Login-page scenarios run logged out (otherwise the form is not even shown);
                    # the session is restored before the next scenario that needs it (WTA-35)
                    if s.logged_out and signed_in:
                        tools.start_logged_out()
                        signed_in = False
                    elif not s.logged_out and login is not None and not signed_in:
                        signed_in = browser.login(login, timeout=15).ok

                    def log_call(call: CallUsage, scenario_page=s.page_id):
                        with local_session() as db:
                            db_crud.create_llm_call(
                                db, job_id, page_id=scenario_page, agent="executor", model=call.model, attempt=call.attempt,
                                outcome=call.outcome, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                                cache_read_input_tokens=call.cache_read_input_tokens,
                                cache_creation_input_tokens=call.cache_creation_input_tokens, cost_usd=call.cost_usd,
                            )

                    result = execute_scenario(s, tools, on_call=log_call, screenshot_path=shots_dir / f"{s.code}.png")
                    with local_session() as db:
                        db_crud.add_scenario_result(
                            db, run_id, s.scenario_id, result.outcome, result.reason,
                            [{"tool": st.tool, "input": st.input, "result": st.result} for st in result.steps],
                            result.screenshot_path,
                        )
                    print(f"[run {run_id}] {s.code}: {result.outcome} ({result.calls} calls)")
                page.close()
    finally:
        with local_session() as db:
            run = db_crud.finish_run(db, run_id, error=error)
            if error is None:
                generate_report(db, job_id, settings.reports_dir)  # the Excel gets its Results sheet
    return {"status": run.status.value if run else "failed", "passed": run.passed if run else 0,
            "failed": run.failed if run else 0, "blocked": run.blocked if run else 0}
