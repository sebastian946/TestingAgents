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
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-xl font-semibold">Account</h1>
      <div className="card p-6 text-sm">
        <p>
          <span className="text-slate-500">Email: </span>
          {user?.email} {user?.email_verified ? <span className="text-emerald-600">(verified)</span> : <span className="text-amber-600">(not verified)</span>}
        </p>
        <p className="mt-1">
          <span className="text-slate-500">Member since: </span>
          {formatDate(user?.created_at ?? null)}
        </p>
        <p className="mt-3">
          <Link to="/billing" className="text-indigo-600 hover:underline">
            Plan and usage
          </Link>
          <span className="mx-2 text-slate-300">·</span>
          <Link to="/forgot-password" className="text-indigo-600 hover:underline">
            Change password
          </Link>
        </p>
      </div>

      <div className="card border-red-200 p-6 dark:border-red-900">
        <h2 className="font-semibold text-red-700 dark:text-red-300">Delete account</h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
          Deletes your account with every job, page, scenario and report. This cannot be undone. If you have a paid
          subscription, cancel it first from the Plan page: deleting the account here does not stop the billing.
        </p>
        <form onSubmit={deleteAccount} className="mt-4 space-y-3">
          <div>
            <label htmlFor="password" className="label">
              Your password
            </label>
            <input id="password" type="password" required autoComplete="current-password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div>
            <label htmlFor="confirm" className="label">
              Type <b>DELETE</b> to confirm
            </label>
            <input id="confirm" required className="input" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} />
          </div>
          <ErrorBox message={error} />
          <button type="submit" className="btn-danger" disabled={busy}>
            {busy ? 'Deleting…' : 'Delete my account'}
          </button>
        </form>
      </div>
    </div>
  )
}
