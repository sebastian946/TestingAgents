import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError, type BillingInfo, type Job, type Run, type Scenario } from '../api'
import { ErrorBox, Notice, formatDate } from './ui'

const REFRESH_MS = 4000

const OUTCOME_CLASS: Record<string, string> = { pass: 'badge-done', fail: 'badge-failed', blocked: 'badge-running' }

/** Automated test runs of a finished job (WTA-33): start one, watch it, read the verdicts. */
export function RunPanel({ job, scenarios, billing }: { job: Job; scenarios: Scenario[]; billing: BillingInfo | null }) {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [current, setCurrent] = useState<Run | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [testEnv, setTestEnv] = useState(false)
  const [withLogin, setWithLogin] = useState(false)
  const [loginUrl, setLoginUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [authorized, setAuthorized] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const scenarioById = new Map(scenarios.map((s) => [s.id, s]))
  const canRun = billing ? billing.plan.test_runs_per_month > 0 : false

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    async function load() {
      try {
        const list = await api.listRuns(job.id)
        if (cancelled) return
        setRuns(list)
        const latest = list[0]
        if (latest) {
          const full = await api.getRun(job.id, latest.id)
          if (cancelled) return
          setCurrent(full)
          if (full.status === 'queued' || full.status === 'running') timer = window.setTimeout(load, REFRESH_MS)
        } else {
          setCurrent(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load the runs.')
      }
    }
    load()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [job.id, reloadKey])

  async function start(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await api.startRun(job.id, {
        test_environment: testEnv,
        login: withLogin ? { login_url: loginUrl.trim() || undefined, username, password, authorized } : undefined,
      })
      setShowForm(false)
      setPassword('')
      setReloadKey((k) => k + 1)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the run.')
    } finally {
      setBusy(false)
    }
  }

  const inProgress = current?.status === 'queued' || current?.status === 'running'
  const doneCount = current ? current.passed + current.failed + current.blocked : 0

  return (
    <section style={{ marginTop: 'var(--space-32)' }}>
      <div className="page-head" style={{ marginBottom: 'var(--space-3)' }}>
        <h2 className="section-title" style={{ margin: 0 }}>
          Automated run
        </h2>
        {job.status === 'done' && !inProgress && !showForm && (
          <button type="button" className="btn-primary btn-sm" onClick={() => setShowForm(true)} disabled={!canRun}>
            <svg viewBox="0 0 24 24">
              <path d="M6 4l14 8-14 8z" />
            </svg>
            Run the tests
          </button>
        )}
      </div>

      {billing && !canRun && (
        <p className="help">
          Automated runs — an AI agent executes every scenario in a real browser and reports what passed — are included in the Pro
          plan. <Link to="/billing">See plans</Link>.
        </p>
      )}
      {billing && canRun && !showForm && !current && (
        <p className="help">
          An AI agent opens each page, follows the steps and compares the result. {billing.runs_remaining} of{' '}
          {billing.plan.test_runs_per_month} runs left this month.
        </p>
      )}
      <ErrorBox message={error} />

      {showForm && (
        <form onSubmit={start} className="card stack" style={{ marginBottom: 'var(--space-16)' }}>
          <Notice variant="warning">
            The agent <b>acts on the site</b>: it fills forms, clicks buttons, submits and, for shop scenarios, goes through the checkout with
            fake card data. Only run it against a <b>test environment</b>. It never deletes, cancels or transfers anything, even if a scenario says so.
          </Notice>
          <label className="check" style={{ alignItems: 'flex-start' }}>
            <input type="checkbox" required checked={testEnv} onChange={(e) => setTestEnv(e.target.checked)} style={{ marginTop: 2 }} />
            <span>I confirm this is a test environment, not production.</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={withLogin} onChange={(e) => setWithLogin(e.target.checked)} />
            The scenarios need a logged-in test account
          </label>
          {withLogin && (
            <div className="stack">
              <div className="field">
                <label htmlFor="run_login_url" className="label">
                  Login page <span className="muted" style={{ fontWeight: 400 }}>(optional, defaults to the site URL)</span>
                </label>
                <input id="run_login_url" type="url" className="input" value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} />
              </div>
              <div className="field-grid">
                <div className="field">
                  <label htmlFor="run_username" className="label">
                    Username or email
                  </label>
                  <input id="run_username" required autoComplete="off" className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="run_password" className="label">
                    Password
                  </label>
                  <input id="run_password" type="password" required autoComplete="off" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
              </div>
              <label className="check" style={{ alignItems: 'flex-start' }}>
                <input type="checkbox" required checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} style={{ marginTop: 2 }} />
                <span>I confirm I am authorized to test this site with this account.</span>
              </label>
            </div>
          )}
          <p className="help">Up to 25 scenarios per run, about 2 minutes each at most. Credentials are encrypted and deleted when the run ends.</p>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn-ghost" onClick={() => setShowForm(false)} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? 'Starting…' : `Run ${Math.min(scenarios.length, 25)} scenarios`}
            </button>
          </div>
        </form>
      )}

      {current && (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div className="row">
              <span className={`badge badge-${current.status}`}>
                <span className="dot" />
                {current.status}
              </span>
              <span className="muted" style={{ fontSize: 13 }}>
                started {formatDate(current.started_at ?? current.created_at)}
                {runs && runs.length > 1 && ` · ${runs.length} runs so far`}
              </span>
            </div>
            <div className="row">
              <span className="badge badge-done">{current.passed} pass</span>
              <span className="badge badge-failed">{current.failed} fail</span>
              <span className="badge badge-running">{current.blocked} blocked</span>
            </div>
          </div>
          {inProgress && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <div className="progress">
                <span style={{ width: `${current.total ? Math.round((doneCount / current.total) * 100) : 0}%` }} />
              </div>
              <p className="help">
                {doneCount} of {current.total} scenarios executed. This page refreshes by itself.
              </p>
            </div>
          )}
          {current.status === 'failed' && current.error && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <ErrorBox message={current.error} />
            </div>
          )}
          {current.results.length > 0 && (
            <div style={{ marginTop: 'var(--space-16)' }}>
              {current.results.map((r) => {
                const s = scenarioById.get(r.scenario_id)
                return (
                  <details key={r.id} className="scenario">
                    <summary>
                      <span className="code">{s?.scenario_code ?? r.scenario_id}</span>
                      <span className="title">{s?.title ?? 'Scenario'}</span>
                      <span className={`badge ${OUTCOME_CLASS[r.outcome] ?? 'badge-queued'}`}>
                        <span className="dot" />
                        {r.outcome}
                      </span>
                    </summary>
                    <div className="scenario-body" style={{ gridTemplateColumns: '1fr' }}>
                      <div>
                        <h4>What the agent observed</h4>
                        <p style={{ margin: 0 }}>{r.reason}</p>
                        {r.has_screenshot && (
                          <p style={{ margin: 'var(--space-2) 0 0' }}>
                            <a href={`/jobs/${job.id}/runs/${current.id}/results/${r.id}/screenshot`} onClick={(e) => openScreenshot(e, job.id, current.id, r.id)}>
                              Final screenshot
                            </a>
                          </p>
                        )}
                      </div>
                      {r.steps.length > 0 && (
                        <div>
                          <h4>Actions</h4>
                          <ol>
                            {r.steps.map((step, i) => (
                              <li key={i}>
                                <span className="tw-mono" style={{ fontSize: 12 }}>
                                  {String((step as { tool?: string }).tool)}
                                </span>{' '}
                                {describeStep(step as { tool?: string; input?: Record<string, unknown>; result?: string })}
                              </li>
                            ))}
                          </ol>
                        </div>
                      )}
                    </div>
                  </details>
                )
              })}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function describeStep(step: { tool?: string; input?: Record<string, unknown>; result?: string }): string {
  const input = step.input ?? {}
  const args = Object.entries(input)
    .filter(([k]) => k !== 'reason')
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(' ')
  const result = step.result ? ` → ${step.result.slice(0, 120)}` : ''
  return `${args}${result}`
}

/** The screenshot needs the bearer header, so it is fetched and opened as a blob. */
async function openScreenshot(e: React.MouseEvent, jobId: string, runId: number, resultId: number) {
  e.preventDefault()
  try {
    const url = await api.resultScreenshot(jobId, runId, resultId)
    window.open(url, '_blank', 'noopener')
  } catch {
    /* the link is still a fallback */
  }
}
