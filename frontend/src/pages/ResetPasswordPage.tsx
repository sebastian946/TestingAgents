import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError } from '../api'
import { useAuth } from '../auth'
import { ErrorBox } from '../components/ui'

/** /reset-password?token=…: the link from the reset email. Sets the password and logs in. */
export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const { resetPassword } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (password !== confirm) {
      setError('The two passwords do not match.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      await resetPassword(token, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reset the password.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="card w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">Choose a new password</h1>
        {!token ? (
          <p className="mt-3 text-sm text-slate-500">This link is missing its token. Request a new one from the log in page.</p>
        ) : (
          <form onSubmit={handleSubmit} className="mt-4 space-y-4">
            <div>
              <label htmlFor="password" className="label">
                New password
              </label>
              <input id="password" type="password" required minLength={8} autoComplete="new-password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
              <p className="mt-1 text-xs text-slate-500">At least 8 characters.</p>
            </div>
            <div>
              <label htmlFor="confirm" className="label">
                Repeat it
              </label>
              <input id="confirm" type="password" required minLength={8} autoComplete="new-password" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </div>
            <ErrorBox message={error} />
            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy ? 'Saving…' : 'Save and log in'}
            </button>
          </form>
        )}
        <p className="mt-4 text-center text-sm text-slate-500">
          <Link to="/login" className="font-medium text-indigo-600 hover:underline">
            Back to log in
          </Link>
        </p>
      </div>
    </div>
  )
}
