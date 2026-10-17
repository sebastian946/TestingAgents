import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError, type Job } from '../api'
import { ErrorBox, StatusBadge, duration, formatDate, hostOf } from '../components/ui'

const REFRESH_MS = 4000

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [error, setError] = useState<string | null>(null)

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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Jobs</h1>
        <Link to="/new" className="btn-primary">
          New job
        </Link>
      </div>
      <ErrorBox message={error} />

      {jobs === null && !error && <p className="text-sm text-slate-500">Loading…</p>}

      {jobs?.length === 0 && (
        <div className="card p-10 text-center">
          <p className="font-medium">No jobs yet</p>
          <p className="mt-1 text-sm text-slate-500">Give it a URL and the agents will explore the site and design test scenarios for every page.</p>
          <Link to="/new" className="btn-primary mt-4">
            Create your first job
          </Link>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500 dark:bg-slate-800/60">
              <tr>
                <th className="px-4 py-2">Site</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2 text-right">Pages</th>
                <th className="px-4 py-2 text-right">Scenarios</th>
                <th className="px-4 py-2">Language</th>
                <th className="px-4 py-2">Created</th>
                <th className="px-4 py-2">Duration</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                  <td className="px-4 py-2">
                    <Link to={`/job/${job.id}`} className="font-medium text-indigo-600 hover:underline">
                      {hostOf(job.url)}
                    </Link>
                    <p className="max-w-xs truncate text-xs text-slate-500" title={job.url}>
                      {job.url}
                    </p>
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">{job.pages_crawled}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{job.total_scenarios}</td>
                  <td className="px-4 py-2 uppercase">{job.language}</td>
                  <td className="px-4 py-2 whitespace-nowrap text-slate-500">{formatDate(job.created_at)}</td>
                  <td className="px-4 py-2 tabular-nums text-slate-500">{duration(job.started_at, job.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
