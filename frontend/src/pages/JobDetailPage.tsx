import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api, ApiError, type Job, type Page, type Scenario, type Usage } from '../api'
import { ErrorBox, PriorityBadge, StatusBadge, duration, formatDate, hostOf } from '../components/ui'

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
  }, [id])

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
    if (!window.confirm('Delete this job, its pages and its scenarios? This cannot be undone.')) return
    setActionError(null)
    try {
      await api.deleteJob(id)
      navigate('/')
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not delete the job.')
    }
  }

  if (error) {
    return (
      <div className="space-y-4">
        <ErrorBox message={error} />
        <Link to="/" className="btn-secondary">
          Back to jobs
        </Link>
      </div>
    )
  }
  if (!data) return <p className="text-sm text-slate-500">Loading…</p>

  const { job, pages, scenarios, usage } = data
  const inProgress = job.status === 'queued' || job.status === 'running'
  const pageById = new Map(pages.map((p) => [p.id, p]))

  return (
    <div className="space-y-6">
      <div>
        <Link to="/" className="text-sm text-slate-500 hover:underline">
          ← Jobs
        </Link>
        <div className="mt-1 flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="flex items-center gap-3 text-xl font-semibold">
              {hostOf(job.url)} <StatusBadge status={job.status} />
            </h1>
            <a href={job.url} target="_blank" rel="noreferrer" className="block truncate text-sm text-slate-500 hover:underline">
              {job.url}
            </a>
          </div>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={download} disabled={job.status !== 'done' || downloading}>
              {downloading ? 'Preparing…' : 'Download Excel report'}
            </button>
            <button type="button" className="btn-danger" onClick={remove} disabled={inProgress}>
              Delete
            </button>
          </div>
        </div>
        <ErrorBox message={actionError} />
      </div>

      {inProgress && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {job.status === 'queued' ? 'Waiting for a worker…' : `Exploring and designing… ${job.pages_crawled} pages, ${job.total_scenarios} scenarios so far.`}{' '}
          This page refreshes by itself.
        </div>
      )}
      {job.status === 'failed' && <ErrorBox message={`The job failed: ${job.error ?? 'unknown reason'}`} />}
      {job.status === 'done' && job.error && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {job.error}
        </div>
      )}

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        <Stat label="Pages" value={job.pages_crawled} />
        <Stat label="Scenarios" value={job.total_scenarios} />
        <Stat label="Language" value={job.language.toUpperCase()} />
        <Stat label="Duration" value={duration(job.started_at, job.finished_at)} />
        <Stat label="Created" value={formatDate(job.created_at)} small />
        <Stat label="LLM cost" value={usage ? `US$${usage.cost_usd.toFixed(4)}` : '—'} hint={usage ? `${usage.calls} calls` : undefined} />
      </dl>

      {job.description && (
        <p className="text-sm text-slate-600 dark:text-slate-300">
          <span className="font-medium">Description: </span>
          {job.description}
        </p>
      )}

      <section>
        <h2 className="mb-2 text-base font-semibold">Pages explored ({pages.length})</h2>
        {pages.length === 0 ? (
          <p className="text-sm text-slate-500">No pages yet.</p>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500 dark:bg-slate-800/60">
                <tr>
                  <th className="px-4 py-2">URL</th>
                  <th className="px-4 py-2">Title</th>
                  <th className="px-4 py-2">Type</th>
                  <th className="px-4 py-2 text-right">Scenarios</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {pages.map((page) => (
                  <tr key={page.id}>
                    <td className="max-w-md truncate px-4 py-2" title={page.url}>
                      {page.url}
                    </td>
                    <td className="px-4 py-2">{page.title ?? '—'}</td>
                    <td className="px-4 py-2">{page.page_type ?? '—'}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{scenarios.filter((s) => s.page_id === page.id).length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">Test scenarios ({scenarios.length})</h2>
        {scenarios.length === 0 ? (
          <p className="text-sm text-slate-500">{inProgress ? 'Scenarios appear as each page is designed.' : 'No scenarios.'}</p>
        ) : (
          <div className="space-y-2">
            {scenarios.map((s) => (
              <details key={s.id} className="card group px-4 py-3">
                <summary className="flex cursor-pointer list-none flex-wrap items-center gap-3">
                  <span className="font-mono text-xs text-slate-500">{s.scenario_code}</span>
                  <span className="flex-1 font-medium">{s.title}</span>
                  <PriorityBadge priority={s.priority} />
                  {s.category && <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">{s.category}</span>}
                  <span className="text-xs text-slate-400 transition group-open:rotate-90">▶</span>
                </summary>
                <div className="mt-3 grid gap-4 text-sm sm:grid-cols-2">
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase text-slate-500">Steps</p>
                    <ol className="list-decimal space-y-1 pl-5">
                      {s.steps.map((step, i) => (
                        <li key={i}>{String(step)}</li>
                      ))}
                    </ol>
                  </div>
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase text-slate-500">Expected result</p>
                    <p>{s.expected_result}</p>
                    <p className="mt-2 truncate text-xs text-slate-400" title={pageById.get(s.page_id)?.url}>
                      {pageById.get(s.page_id)?.url}
                    </p>
                  </div>
                </div>
              </details>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function Stat({ label, value, hint, small }: { label: string; value: string | number; hint?: string; small?: boolean }) {
  return (
    <div className="card px-4 py-3">
      <dt className="text-xs uppercase text-slate-500">{label}</dt>
      <dd className={`${small ? 'text-sm' : 'text-lg'} font-semibold tabular-nums`}>{value}</dd>
      {hint && <dd className="text-xs text-slate-400">{hint}</dd>}
    </div>
  )
}
