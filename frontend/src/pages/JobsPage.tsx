import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api, ApiError, type Job } from '../api'
import { ErrorBox, LANGUAGE_NAMES, StatusBadge, duration, formatDate, hostOf } from '../components/ui'

const REFRESH_MS = 4000

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    async function load() {
      try {
        const list = await api.listJobs()
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

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">Jobs</h1>
        <Link to="/new" className="btn-primary">
          <svg viewBox="0 0 24 24">
            <path d="M12 5v14M5 12h14" />
          </svg>
          New job
        </Link>
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
          <p>Paste a URL and our agents will explore up to 8 pages and write the test scenarios for you — in English or Spanish.</p>
          <Link to="/new" className="btn-primary">
            Create your first job
          </Link>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Site</th>
                <th>Status</th>
                <th className="num">Pages</th>
                <th className="num">Scenarios</th>
                <th>Language</th>
                <th>Created</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
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
                  <td className="num">{job.pages_crawled}</td>
                  <td className="num">{job.total_scenarios}</td>
                  <td className="meta">{LANGUAGE_NAMES[job.language] ?? job.language}</td>
                  <td className="meta">{formatDate(job.created_at)}</td>
                  <td className="meta">{duration(job.started_at, job.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
