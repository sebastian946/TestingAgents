# ADR-10: The Executor runs scenarios in a real browser, with tools, not an MCP server

- **Status:** Accepted
- **Date:** 2026-10-21
- **Ticket:** WTA-33

## Context

The product writes test scenarios; customers still have to run them by hand. The next
step is obvious and valuable: run them automatically and report what passed, what failed
and why. Unlike the crawl (ADR-01), this cannot be deterministic: following "fill the form
with valid data and submit" on an arbitrary site needs judgement, so an LLM has to drive
the browser.

Three questions:

1. **How does the model touch the browser?** The Playwright MCP server (Node) is the
   fashionable answer. We already run Playwright in Python in the worker, with the login,
   the anti-SSRF checks and the resource blocking built around it.
2. **What is the agent allowed to do?** It acts as a real user on the customer's site: it
   submits forms. The crawl only reads.
3. **What does it cost and who pays?** An agentic loop is 5–20 model calls per scenario
   with the page in context: US$0.10–0.40 per scenario on Sonnet, several dollars per job.

## Decision

- **Tools over MCP.** The Executor exposes six tools to the Messages API tool-use loop
  (`navigate`, `read_page`, `click`, `fill`, `press`, `finish`), implemented on the
  worker's own Playwright page (`agents/executor.py`). It is the same contract an MCP
  server offers, without a second process, a Node runtime, or a transport to secure. An
  MCP server of *ours* (so Claude Desktop or an IDE can use Tests Wise) stays on the
  roadmap; it is a different product surface.
- **The page is text, not pixels.** `read_page` returns the URL, title, visible text
  (truncated) and an inventory of inputs, buttons, links and alerts. No screenshot is sent
  to the model: cheaper, and no customer pixels leave the server. A screenshot of the final
  state is saved locally as evidence.
- **Guardrails in the tools, not in the prompt.** The browser stays on the job's host;
  clicks, fills and navigations on anything with a forbidden word (`pay`, `purchase`,
  `delete`, `cancel`, `unsubscribe`, ...) are refused and the refusal is returned to the
  model, whatever the scenario asked. "Submit", "send" and "confirm" are allowed: submitting
  a form is what a scenario tests. A step cap and a time cap turn a stuck scenario into
  `blocked`. The prompt repeats the rules, but the tools enforce them.
- **Test environments only, by confirmation.** `POST /jobs/{id}/runs` requires
  `test_environment: true`, and the dashboard says in plain words that the agent fills
  forms and clicks. Credentials for the run follow ADR-08: encrypted, used once, deleted
  when the run ends.
- **Sonnet, with a budget per run.** `EXECUTOR_MODEL` defaults to Sonnet 5.5 (Opus designs
  the scenarios; following steps needs less); at most 25 scenarios per run, 20 steps and 2
  minutes per scenario. Every call is logged in `llm_calls` with `agent = executor`
  (ADR-06), so the real cost per run is a query.
- **Pro only, N per month.** `Plan.test_runs_per_month` (Pro: 10). The run counts when it
  starts, like a job; `402` past the allowance.
- **Verdicts are data.** `test_runs` and `scenario_results` rows, one commit per scenario
  for live progress, and a Results sheet in the same Excel report.

## Consequences

- Customers get a pass/fail report with reasons, not just a plan. The agent is honest
  about what it could not do (`blocked`), which keeps the numbers trustworthy.
- The agent cannot pay, buy, delete or cancel anything, so scenarios about those flows come
  back `blocked`. That is the right trade for a tool that runs unattended on someone
  else's site.
- Flaky sites and ambiguous expected results produce some wrong verdicts. Each one is a
  prompt or tool improvement, measured with the saved step logs; the design mirrors the
  page classifier's loop (ADR-04).
- Cost is bounded per run but not small; the plan allowance is the price control, and the
  `llm_calls` ledger shows whether it holds.
- Fake mode (`DESIGNER_MODE=fake`) passes any page that loads, so the whole run pipeline
  is covered by tests without a model.
