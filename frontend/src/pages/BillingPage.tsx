import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api, ApiError, type BillingInfo } from '../api'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ErrorBox, Notice, formatDate } from '../components/ui'

const PLAN_FEATURES: Record<string, string[]> = {
  free: ['Up to 8 pages per job', 'Excel report', 'English and Spanish scenarios'],
  starter: ['Up to 8 pages per job', 'Sites behind a login', 'English and Spanish scenarios'],
  pro: ['Everything in Starter', '10 automated test runs per month', '7-day free trial'],
}

export function BillingPage() {
  const [info, setInfo] = useState<BillingInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [params, setParams] = useSearchParams()
  const wanted = params.get('plan')
  const redirected = useRef(false)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [busy, setBusy] = useState<'cancel' | 'resume' | null>(null)

  async function change(action: 'cancel' | 'resume') {
    setError(null)
    setBusy(action)
    try {
      setInfo(action === 'cancel' ? await api.cancelPlan() : await api.resumePlan())
      setConfirmCancel(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change the subscription.')
    } finally {
      setBusy(null)
    }
  }

  useEffect(() => {
    api
      .billing()
      .then(setInfo)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load your plan.'))
  }, [])

  // /billing?plan=starter (from the landing or the register page): go straight to the hosted
  // checkout when it exists; otherwise fall through and explain why the button is missing
  useEffect(() => {
    if (!info || !wanted || redirected.current) return
    const url = info.checkout_urls[wanted]
    if (url && wanted !== info.plan.code) {
      redirected.current = true
      window.location.assign(url)
    }
  }, [info, wanted])

  if (error && !info) return <ErrorBox message={error} />
  if (!info) return <p className="muted">Loading…</p>
  const wantedPlan = wanted ? info.plans.find((p) => p.code === wanted) : undefined

  const percent = Math.min(100, Math.round((info.used_this_month / info.plan.jobs_per_month) * 100))
  const billingConfigured = Object.keys(info.checkout_urls).length > 0

  return (
    <div style={{ maxWidth: '56rem', margin: '0 auto' }}>
      <div className="page-head">
        <h1 className="page-title">Plan and usage</h1>
      </div>

      {wantedPlan && wantedPlan.code === info.plan.code && (
        <div style={{ marginBottom: 'var(--space-16)' }}>
          <Notice variant="success">You are already on the {wantedPlan.name} plan.</Notice>
        </div>
      )}
      {wantedPlan && wantedPlan.code !== info.plan.code && info.checkout_urls[wantedPlan.code] && (
        <div style={{ marginBottom: 'var(--space-16)' }}>
          <Notice variant="info">
            Opening the payment page for {wantedPlan.name}…{' '}
            <a href={info.checkout_urls[wantedPlan.code]}>Click here if it does not open.</a>
          </Notice>
        </div>
      )}
      {wantedPlan && wantedPlan.code !== info.plan.code && !info.checkout_urls[wantedPlan.code] && (
        <div style={{ marginBottom: 'var(--space-16)' }}>
          <Notice variant="warning">
            Paid plans open in a few days: you are on Free meanwhile, with everything except the higher allowance. Write to{' '}
            <a href="mailto:hola@tests-wise.com">hola@tests-wise.com</a> and we will tell you the moment {wantedPlan.name} is available.{' '}
            <button type="button" className="link" onClick={() => setParams({})}>
              Dismiss
            </button>
          </Notice>
        </div>
      )}
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
          <div className="row">
            {info.can_resume && (
              <button type="button" className="btn-primary btn-sm" onClick={() => change('resume')} disabled={busy !== null}>
                {busy === 'resume' ? 'Resuming…' : 'Keep my plan'}
              </button>
            )}
            {info.customer_portal_url && (
              <a href={info.customer_portal_url} target="_blank" rel="noreferrer" className="btn-secondary btn-sm">
                Invoices and card
              </a>
            )}
            {info.can_cancel && (
              <button type="button" className="btn-danger btn-sm" onClick={() => setConfirmCancel(true)} disabled={busy !== null}>
                Cancel plan
              </button>
            )}
          </div>
        </div>
        <ErrorBox message={info ? error : null} />
        {info.status === 'cancelled' && info.current_period_end && (
          <p className="help" style={{ marginTop: 'var(--space-8)' }}>
            Cancelled: you keep {info.plan.name} until {formatDate(info.current_period_end)} and will not be charged again.
          </p>
        )}
        {info.status === 'on_trial' && info.current_period_end && (
          <p className="help" style={{ marginTop: 'var(--space-8)' }}>
            Free trial: the first charge happens on {formatDate(info.current_period_end)} unless you cancel before.
          </p>
        )}
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
                  {plan.trial_days > 0 && !info.status ? `Try free for ${plan.trial_days} days` : plan.price_usd > info.plan.price_usd ? 'Upgrade' : 'Switch'}
                </a>
              ) : plan.price_usd > 0 && !billingConfigured ? (
                <p className="help" style={{ marginTop: 'auto' }}>
                  Opens in a few days. <a href="mailto:hola@tests-wise.com?subject=Early%20access">Ask for early access</a>.
                </p>
              ) : info.can_cancel ? (
                <p className="help" style={{ marginTop: 'auto' }}>
                  Use “Cancel plan” above to return to Free at the end of the period.
                </p>
              ) : null}
            </div>
          )
        })}
      </div>
      <p className="help" style={{ marginTop: 'var(--space-24)' }}>
        Payments are processed by Lemon Squeezy, which handles taxes and invoices. Your card details never reach our servers. Prices in USD.
        Refunds within 14 days of a charge: write to <a href="mailto:hola@tests-wise.com">hola@tests-wise.com</a>.
      </p>
      <ConfirmDialog
        open={confirmCancel}
        title="Cancel your plan?"
        confirmLabel="Cancel plan"
        busy={busy === 'cancel'}
        onConfirm={() => change('cancel')}
        onCancel={() => setConfirmCancel(false)}
      >
        You keep <b>{info.plan.name}</b>
        {info.current_period_end ? ` until ${formatDate(info.current_period_end)}` : ' until the end of the paid period'} and will not be charged again. You can undo this
        from the same page while the period lasts.
      </ConfirmDialog>
    </div>
  )
}
