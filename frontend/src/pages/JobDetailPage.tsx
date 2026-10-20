import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api, ApiError, type Job, type Page, type Scenario, type Usage } from '../api'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ErrorBox, LANGUAGE_NAMES, Notice, PriorityBadge, StatusBadge, duration, formatDate, hostOf } from '../components/ui'

const REFRESH_MS = 3000

interface Loaded {
  job: Job
  pages: Page[]
  scenarios: Scenario[]
  usage: Usage | null
}

export function JobDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [picked, setPicked] = useState<Set<number> | null>(null)
  const [submittingSelection, setSubmittingSelection] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    async function load() {
      try {
        const job = await api.getJob(id)
        const [pages, scenarios] = await Promise.all([api.getPages(id), api.getScenarios(id)])
        // Usage is a cost detail: a failure there must not hide the job
        const usage = await api.getUsage(id).catch(() => null)
        if (cancelled) return
        setData({ job, pages, scenarios, usage })
        setError(null)
        if (job.status === 'awaiting_selection') setPicked((prev) => prev ?? new Set(pages.filter((p) => p.selected).map((p) => p.id)))
        if (job.status === 'queued' || job.status === 'running') timer = window.setTimeout(load, REFRESH_MS)
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load the job.')
      }
    }
    load()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [id, reloadKey])

  async function submitSelection() {
    if (!picked || picked.size === 0) return
    setActionError(null)
    setSubmittingSelection(true)
    try {
      await api.selectPages(id, [...picked])
      setPicked(null)
      setReloadKey((k) => k + 1) // the job is queued again: resume polling
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not start the scenarios.')
    } finally {
      setSubmittingSelection(false)
    }
  }

  async function download() {
    setActionError(null)
    setDownloading(true)
    try {
      await api.downloadReport(id)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not download the report.')
    } finally {
      setDownloading(false)
    }
  }

  async function remove() {
    setActionError(null)
    setDeleting(true)
    try {
      await api.deleteJob(id)
      navigate('/dashboard')
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not delete the job.')
      setConfirmDelete(false)
    } finally {
      setDeleting(false)
    }
  }

  if (error) {
    return (
      <div className="stack">
        <ErrorBox message={error} />
        <Link to="/dashboard" className="btn-secondary">
          Back to jobs
        </Link>
      </div>
    )
  }
  if (!data) return <p className="muted">Loading…</p>

  const { job, pages, scenarios, usage } = data
  const inProgress = job.status === 'queued' || job.status === 'running'
  const choosing = job.status === 'awaiting_selection' && picked !== null
  const pageById = new Map(pages.map((p) => [p.id, p]))

  function togglePage(pageId: number) {
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(pageId)) next.delete(pageId)
      else next.add(pageId)
      return next
    })
  }

  return (
    <div className="stack" style={{ gap: 0 }}>
      <div>
        <Link to="/dashboard" className="back-link">
          ← Jobs
        </Link>
        <div className="page-head" style={{ marginTop: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
          <div style={{ minWidth: 0 }}>
            <h1 className="section-title row" style={{ marginBottom: 'var(--space-1)' }}>
              {hostOf(job.url)} <StatusBadge status={job.status} />
            </h1>
            <a href={job.url} target="_blank" rel="noreferrer" className="tw-mono muted" style={{ fontSize: 13, wordBreak: 'break-all' }}>
              {job.url}
            </a>
          </div>
          <div className="row">
            <button type="button" className="btn-primary" onClick={download} disabled={job.status !== 'done' || downloading}>
              <svg viewBox="0 0 24 24">
                <path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
              </svg>
              {downloading ? 'Preparing…' : 'Download Excel report'}
            </button>
            <button type="button" className="btn-danger" onClick={() => setConfirmDelete(true)} disabled={inProgress}>
              <svg viewBox="0 0 24 24">
                <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
              </svg>
              Delete
            </button>
          </div>
        </div>
        <ErrorBox message={actionError} />
      </div>

      {inProgress && (
        <Notice variant="warning">
          {job.status === 'queued' ? (
            'Waiting for a worker…'
          ) : (
            <>
              Exploring… <b>{job.pages_crawled} pages, {job.total_scenarios} scenarios</b> so far.
            </>
          )}{' '}
          This page refreshes by itself.
        </Notice>
      )}
      {choosing && (
        <Notice variant="info">
          We found <b>{pages.length} pages</b>. Tick the ones that should get test scenarios and press <b>Generate scenarios</b>. Only those
          pages count toward your plan's cost.
        </Notice>
      )}
      {job.status === 'failed' && <ErrorBox message={`The job failed: ${job.error ?? 'unknown reason'}`} />}
      {job.status === 'done' && job.error && <Notice variant="warning">{job.error}</Notice>}

      <div style={{ display: 'grid', gap: 'var(--space-3)', gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))', marginTop: 'var(--space-4)' }}>
        <Stat label="Pages" value={job.pages_crawled} />
        <Stat label="Scenarios" value={job.total_scenarios} />
        <Stat label="Language" value={LANGUAGE_NAMES[job.language] ?? job.language} small />
        <Stat label="Duration" value={duration(job.started_at, job.finished_at)} small />
        <Stat label="Created" value={formatDate(job.created_at)} small />
        <Stat label="LLM cost" value={usage ? `$${usage.cost_usd.toFixed(3)}` : '—'} hint={usage ? `${usage.calls} calls` : undefined} mono />
      </div>

      {(job.login_url || job.description) && (
        <div className="muted" style={{ marginTop: 'var(--space-4)', fontSize: 14 }}>
          {job.login_url && (
            <p style={{ margin: '0 0 var(--space-1)' }}>
              <b style={{ color: 'var(--ink)' }}>Explored logged in</b> via{' '}
              <a href={job.login_url} target="_blank" rel="noreferrer">
                {job.login_url}
              </a>
              . The credentials were deleted when the exploration ended.
            </p>
          )}
          {job.description && (
            <p style={{ margin: 0 }}>
              <b style={{ color: 'var(--ink)' }}>Description: </b>
              {job.description}
            </p>
          )}
        </div>
      )}

      <section style={{ marginTop: 'var(--space-8)' }}>
        <div className="page-head" style={{ marginBottom: 'var(--space-3)' }}>
          <h2 className="section-title" style={{ margin: 0 }}>
            Pages explored ({pages.length})
          </h2>
          {choosing && (
            <div className="row">
              <button type="button" className="btn-ghost btn-sm" onClick={() => setPicked(new Set(pages.map((p) => p.id)))}>
                Select all
              </button>
              <button type="button" className="btn-ghost btn-sm" onClick={() => setPicked(new Set())}>
                Select none
              </button>
              <button type="button" className="btn-primary" onClick={submitSelection} disabled={submittingSelection || picked.size === 0}>
                {submittingSelection ? 'Starting…' : `Generate scenarios for ${picked.size} page${picked.size === 1 ? '' : 's'}`}
              </button>
            </div>
          )}
        </div>
        {pages.length === 0 ? (
          <p className="muted">No pages yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {choosing && <th style={{ width: 40 }} />}
                  <th>URL</th>
                  <th>Title</th>
                  <th>Type</th>
                  <th className="num">Scenarios</th>
                </tr>
              </thead>
              <tbody>
                {pages.map((page) => (
                  <tr
                    key={page.id}
                    className={choosing ? 'is-link' : undefined}
                    onClick={choosing ? () => togglePage(page.id) : undefined}
                    style={!choosing && job.select_pages && !page.selected ? { opacity: 0.55 } : undefined}
                  >
                    {choosing && (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" checked={picked.has(page.id)} onChange={() => togglePage(page.id)} aria-label={`Include ${page.url}`} style={{ accentColor: 'var(--brand)', width: 18, height: 18 }} />
                      </td>
                    )}
                    <td>
                      <span className="url" title={page.url}>
                        {page.url}
                      </span>
                    </td>
                    <td>{page.title ?? '—'}</td>
                    <td className="meta">{page.page_type ?? '—'}</td>
                    <td className="num">{!choosing && job.select_pages && !page.selected ? <span className="meta">skipped</span> : scenarios.filter((s) => s.page_id === page.id).length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section style={{ marginTop: 'var(--space-8)' }}>
        <h2 className="section-title">Test scenarios ({scenarios.length})</h2>
        {scenarios.length === 0 ? (
          <p className="muted">{inProgress ? 'Scenarios appear as each page is designed.' : 'No scenarios.'}</p>
        ) : (
          <div>
            {scenarios.map((s) => (
              <details key={s.id} className="scenario">
                <summary>
                  <span className="code">{s.scenario_code}</span>
                  <span className="title">{s.title}</span>
                  <PriorityBadge priority={s.priority} />
                  {s.category && <span className="chip">{s.category}</span>}
                </summary>
                <div className="scenario-body">
                  <div>
                    <h4>Steps</h4>
                    <ol>
                      {s.steps.map((step, i) => (
                        <li key={i}>{String(step)}</li>
                      ))}
                    </ol>
                  </div>
                  <div>
                    <h4>Expected result</h4>
                    <p style={{ margin: 0 }}>{s.expected_result}</p>
                    <span className="url" title={pageById.get(s.page_id)?.url}>
                      {pageById.get(s.page_id)?.url}
                    </span>
                  </div>
                </div>
              </details>
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog open={confirmDelete} title="Delete this job?" confirmLabel="Delete job" busy={deleting} onConfirm={remove} onCancel={() => setConfirmDelete(false)}>
        The report for <b style={{ color: 'var(--ink)' }}>{hostOf(job.url)}</b> and its {job.total_scenarios} scenarios will be removed. This cannot be
        undone.
      </ConfirmDialog>
    </div>
  )
}

function Stat({ label, value, hint, small, mono }: { label: string; value: string | number; hint?: string; small?: boolean; mono?: boolean }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value${small ? ' is-small' : ''}${mono ? ' tw-mono' : ''}`}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}
