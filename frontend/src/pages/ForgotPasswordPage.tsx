import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../api'
import { ErrorBox } from '../components/ui'

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await api.forgotPassword(email)
      setSent(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="card w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">Reset your password</h1>
        {sent ? (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-300">
            If <b>{email}</b> has an account, a reset link is on its way. It works for a limited time.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="mt-4 space-y-4">
            <p className="text-sm text-slate-500">Enter your email and we will send you a link to choose a new password.</p>
            <div>
              <label htmlFor="email" className="label">
                Email
              </label>
              <input id="email" type="email" required autoComplete="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <ErrorBox message={error} />
            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy ? 'Sending…' : 'Send reset link'}
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
