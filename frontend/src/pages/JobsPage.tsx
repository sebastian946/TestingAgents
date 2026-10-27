import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api, ApiError, type Job, type JobStatus } from '../api'
import { ErrorBox, LANGUAGE_NAMES, StatusBadge, duration, formatDate, hostOf } from '../components/ui'

const REFRESH_MS = 4000
const PAGE_SIZE = 25
const STATUSES: (JobStatus | 'all')[] = ['all', 'queued', 'running', 'awaiting_selection', 'done', 'failed']

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<JobStatus | 'all'>('all')
  const [page, setPage] = useState(0)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    async function load() {
      try {
        const list = await api.listJobs(200)
        if (cancelled) return
        setJobs(list)
        setError(null)
        // Keep refreshing only while something is in progress (ADR-03: clients poll)
        if (list.some((j) => j.status === 'queued' || j.status === 'running')) {
          timer = window.setTimeout(load, REFRESH_MS)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load the jobs.')
      }
    }
    load()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [])

  const filtered = (jobs ?? []).filter((j) => (statusFilter === 'all' || j.status === statusFilter) && (!query || j.url.toLowerCase().includes(query.toLowerCase())))
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const current = Math.min(page, pageCount - 1)
  const visible = filtered.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE)

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">Jobs</h1>
      </div>
      <ErrorBox message={error} />

      {jobs === null && !error && <p className="muted">Loading…</p>}

      {jobs?.length === 0 && (
        <div className="card empty" style={{ maxWidth: '40rem', margin: '0 auto' }}>
          <div className="glyph">
            <svg viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="9" />
              <path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18" />
            </svg>
          </div>
          <h2 className="card-title">No jobs yet</h2>
          <p>Paste a URL and the AI agents will explore up to 8 pages and write the test scenarios automatically — in English or Spanish.</p>
          <Link to="/new" className="btn-primary">
            Create your first job
          </Link>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="table-wrap">
          <div className="table-toolbar">
            <label className="search">
              <svg className="i" viewBox="0 0 24 24">
                <circle cx="11" cy="11" r="7" />
                <path d="M20 20l-3.5-3.5" />
              </svg>
              <input type="search" placeholder="Search jobs…" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0) }} aria-label="Search jobs" />
            </label>
            <select className="select" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value as JobStatus | 'all'); setPage(0) }} aria-label="Filter by status">
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s === 'all' ? 'Status: all' : s === 'awaiting_selection' ? 'choose pages' : s}
                </option>
              ))}
            </select>
            <span className="spacer" />
            <Link to="/new" className="btn-primary btn-sm hide-sm">
              <svg viewBox="0 0 24 24">
                <path d="M12 5v14M5 12h14" />
              </svg>
              New job
            </Link>
          </div>
          <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Site</th>
                <th>Status</th>
                <th className="num hide-sm">Pages</th>
                <th className="num hide-sm">Scenarios</th>
                <th className="hide-sm">Language</th>
                <th className="hide-sm">Created</th>
                <th className="hide-sm">Duration</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((job) => (
                <tr key={job.id} className="is-link" onClick={() => navigate(`/job/${job.id}`)}>
                  <td>
                    <Link to={`/job/${job.id}`} className="host" style={{ color: 'inherit' }} onClick={(e) => e.stopPropagation()}>
                      {hostOf(job.url)}
                    </Link>
                    <span className="url" title={job.url}>
                      {job.url}
                    </span>
                  </td>
                  <td>
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="num hide-sm">{job.pages_crawled}</td>
                  <td className="num hide-sm">{job.total_scenarios}</td>
                  <td className="meta hide-sm">{LANGUAGE_NAMES[job.language] ?? job.language}</td>
                  <td className="meta hide-sm">{formatDate(job.created_at)}</td>
                  <td className="meta hide-sm">{duration(job.started_at, job.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <div className="table-footer">
            <span>
              {filtered.length === 0 ? 'No jobs match' : `Showing ${current * PAGE_SIZE + 1}–${Math.min(filtered.length, (current + 1) * PAGE_SIZE)} of ${filtered.length}`}
            </span>
            <span className="row">
              <button type="button" className="btn-outline btn-sm" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={current === 0}>
                Previous
              </button>
              <button type="button" className="btn-outline btn-sm" onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={current >= pageCount - 1}>
                Next
              </button>
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
