import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api, ApiError } from '../api'
import { useAuth } from '../auth'
import { ErrorBox, formatDate } from '../components/ui'

export function AccountPage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirmText, setConfirmText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function deleteAccount(event: FormEvent) {
    event.preventDefault()
    if (confirmText !== 'DELETE') {
      setError('Type DELETE to confirm.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      await api.deleteAccount(password)
      logout()
      navigate('/login', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the account.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ maxWidth: '36rem', margin: '0 auto' }}>
      <div className="page-head">
        <h1 className="page-title">Account</h1>
      </div>
      <div className="card">
        <div className="stat-label">Email</div>
        <p style={{ margin: '0 0 var(--space-3)' }}>
          {user?.email}{' '}
          {user?.email_verified ? (
            <span className="badge badge-done">verified</span>
          ) : (
            <span className="badge badge-running">not verified</span>
          )}
        </p>
        <div className="stat-label">Member since</div>
        <p style={{ margin: '0 0 var(--space-4)' }}>{formatDate(user?.created_at ?? null)}</p>
        <div className="row">
          <Link to="/billing" className="btn-secondary btn-sm">
            Plan and usage
          </Link>
          <Link to="/forgot-password" className="btn-ghost btn-sm">
            Change password
          </Link>
        </div>
      </div>

      <div className="card" style={{ marginTop: 'var(--space-6)', borderColor: 'var(--status-failed-ink)' }}>
        <h2 className="card-title" style={{ color: 'var(--status-failed-ink)' }}>
          Delete account
        </h2>
        <p className="muted" style={{ margin: '0 0 var(--space-4)' }}>
          Deletes your account with every job, page, scenario and report. This cannot be undone. If you have a paid subscription, cancel it
          first from the Plan page: deleting the account here does not stop the billing.
        </p>
        <form onSubmit={deleteAccount}>
          <div className="field">
            <label htmlFor="password" className="label">
              Your password
            </label>
            <input id="password" type="password" required autoComplete="current-password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="confirm" className="label">
              Type <b>DELETE</b> to confirm
            </label>
            <input id="confirm" required className="input" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} />
          </div>
          {error && (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <ErrorBox message={error} />
            </div>
          )}
          <button type="submit" className="btn-danger" style={{ marginTop: 'var(--space-6)' }} disabled={busy}>
            {busy ? 'Deleting…' : 'Delete my account'}
          </button>
        </form>
      </div>
    </div>
  )
}
