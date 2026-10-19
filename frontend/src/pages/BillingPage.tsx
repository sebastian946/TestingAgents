import { useEffect, useState } from 'react'

import { api, ApiError, type BillingInfo } from '../api'
import { ErrorBox, formatDate } from '../components/ui'

export function BillingPage() {
  const [info, setInfo] = useState<BillingInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .billing()
      .then(setInfo)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load your plan.'))
  }, [])

  if (error) return <ErrorBox message={error} />
  if (!info) return <p className="text-sm text-slate-500">Loading…</p>

  const percent = Math.min(100, Math.round((info.used_this_month / info.plan.jobs_per_month) * 100))
  const billingConfigured = Object.keys(info.checkout_urls).length > 0

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-xl font-semibold">Plan and usage</h1>

      <div className="card p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p>
            You are on the <b>{info.plan.name}</b> plan
            {info.status && info.status !== 'active' && <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">{info.status.replace('_', ' ')}</span>}
          </p>
          <p className="text-sm text-slate-500">
            {info.used_this_month} of {info.plan.jobs_per_month} jobs used this month
          </p>
        </div>
        <div className="mt-3 h-2 w-full overflow-hidden rounded bg-slate-200 dark:bg-slate-800">
          <div className={`h-full ${percent >= 100 ? 'bg-red-500' : 'bg-indigo-600'}`} style={{ width: `${percent}%` }} />
        </div>
        {info.current_period_end && (
          <p className="mt-2 text-xs text-slate-500">
            {info.status === 'cancelled' ? 'Access ends' : 'Renews'} on {formatDate(info.current_period_end)}.
          </p>
        )}
        {info.customer_portal_url && (
          <a href={info.customer_portal_url} target="_blank" rel="noreferrer" className="btn-secondary mt-4">
            Manage subscription (invoices, payment method, cancel)
          </a>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {info.plans.map((plan) => {
          const current = plan.code === info.plan.code
          const checkout = info.checkout_urls[plan.code]
          return (
            <div key={plan.code} className={`card p-5 ${current ? 'ring-2 ring-indigo-500' : ''}`}>
              <p className="text-sm font-medium text-slate-500">{plan.name}</p>
              <p className="mt-1 text-2xl font-semibold">
                {plan.price_usd === 0 ? 'Free' : `US$${plan.price_usd}`}
                {plan.price_usd > 0 && <span className="text-sm font-normal text-slate-500">/month</span>}
              </p>
              <p className="mt-2 text-sm">{plan.jobs_per_month} jobs per month</p>
              <p className="text-xs text-slate-500">Up to 8 pages and the Excel report per job</p>
              {current ? (
                <p className="mt-4 text-sm font-medium text-indigo-600">Current plan</p>
              ) : checkout ? (
                <a href={checkout} className="btn-primary mt-4 w-full">
                  {plan.price_usd > info.plan.price_usd ? 'Upgrade' : 'Switch'}
                </a>
              ) : plan.price_usd > 0 && !billingConfigured ? (
                <p className="mt-4 text-xs text-slate-500">Payments are not enabled on this server yet.</p>
              ) : null}
            </div>
          )
        })}
      </div>
      <p className="text-xs text-slate-500">
        Payments are processed by Lemon Squeezy, which handles taxes and invoices. Your card details never reach our servers.
      </p>
    </div>
  )
}
