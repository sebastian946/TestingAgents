import { useEffect, useState } from 'react'

import { api, ApiError, type BillingInfo } from '../api'
import { ErrorBox, formatDate } from '../components/ui'

const PLAN_FEATURES: Record<string, string[]> = {
  free: ['Up to 8 pages per job', 'Excel report', 'English and Spanish scenarios'],
  starter: ['Up to 8 pages per job', 'Sites behind a login', 'English and Spanish scenarios'],
  pro: ['Everything in Starter', '10 automated test runs per month', 'Priority support'],
}

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
  if (!info) return <p className="muted">Loading…</p>

  const percent = Math.min(100, Math.round((info.used_this_month / info.plan.jobs_per_month) * 100))
  const billingConfigured = Object.keys(info.checkout_urls).length > 0

  return (
    <div style={{ maxWidth: '56rem', margin: '0 auto' }}>
      <div className="page-head">
        <h1 className="page-title">Plan and usage</h1>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="stat">
            <div className="stat-label">Current plan</div>
            <div className="stat-value" style={{ fontSize: 24, lineHeight: 1.33 }}>
              {info.plan.name}
              {info.status && info.status !== 'active' && (
                <span className="chip" style={{ marginLeft: 'var(--space-8)', verticalAlign: 'middle' }}>
                  {info.status.replace('_', ' ')}
                </span>
              )}
            </div>
          </div>
          {info.customer_portal_url && (
            <a href={info.customer_portal_url} target="_blank" rel="noreferrer" className="btn-secondary btn-sm">
              Manage subscription
            </a>
          )}
        </div>
        <p className="muted" style={{ margin: 'var(--space-12) 0 var(--space-8)' }}>
          <b style={{ color: 'var(--ink)' }}>
            {info.used_this_month} of {info.plan.jobs_per_month}
          </b>{' '}
          jobs used this month
          {info.current_period_end && ` · ${info.status === 'cancelled' ? 'access ends' : 'renews'} ${formatDate(info.current_period_end)}`}
        </p>
        <div className={`progress${percent >= 100 ? ' is-full' : ''}`}>
          <span style={{ width: `${percent}%` }} />
        </div>
        {info.plan.test_runs_per_month > 0 && (
          <p className="muted" style={{ margin: 'var(--space-12) 0 0' }}>
            <b style={{ color: 'var(--ink)' }}>
              {info.runs_this_month} of {info.plan.test_runs_per_month}
            </b>{' '}
            automated test runs used this month
          </p>
        )}
      </div>

      <div style={{ display: 'grid', gap: 'var(--space-16)', gridTemplateColumns: 'repeat(auto-fit, minmax(14rem, 1fr))', marginTop: 'var(--space-24)' }}>
        {info.plans.map((plan) => {
          const current = plan.code === info.plan.code
          const checkout = info.checkout_urls[plan.code]
          return (
            <div key={plan.code} className={`card plan${current ? ' is-current' : ''}`}>
              {plan.code === 'starter' && !current && <span className="ribbon">Most popular</span>}
              <div className="stat-label">{plan.name}</div>
              <div className="price">
                ${plan.price_usd}
                <small>/month</small>
              </div>
              <ul>
                <li>{plan.jobs_per_month} jobs per month</li>
                {(PLAN_FEATURES[plan.code] ?? []).map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
              {current ? (
                <button type="button" className="btn-secondary" style={{ marginTop: 'auto' }} disabled>
                  Current plan
                </button>
              ) : checkout ? (
                <a href={checkout} className={plan.code === 'starter' ? 'btn-primary' : 'btn-outline'} style={{ marginTop: 'auto' }}>
                  {plan.price_usd > info.plan.price_usd ? 'Upgrade' : 'Switch'}
                </a>
              ) : plan.price_usd > 0 && !billingConfigured ? (
                <p className="help" style={{ marginTop: 'auto' }}>
                  Payments are not enabled on this server yet.
                </p>
              ) : info.customer_portal_url ? (
                <p className="help" style={{ marginTop: 'auto' }}>
                  Change or cancel from “Manage subscription”.
                </p>
              ) : null}
            </div>
          )
        })}
      </div>
      <p className="help" style={{ marginTop: 'var(--space-24)' }}>
        Payments are processed by Lemon Squeezy, which handles taxes and invoices. Your card details never reach our servers. Prices in USD.
      </p>
    </div>
  )
}
