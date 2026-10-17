import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../api'
import { ErrorBox } from '../components/ui'

interface Props {
  title: string
  submitLabel: string
  onSubmit: (email: string, password: string) => Promise<void>
  footer: { text: string; linkText: string; to: string }
}

/** The one form behind /login and /register: they differ only in the call they make. */
export function AuthForm({ title, submitLabel, onSubmit, footer }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await onSubmit(email, password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="card w-full max-w-sm p-6">
        <div className="mb-6 flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-600 text-white">✓</span>
          <div>
            <p className="text-xs text-slate-500">Testing Agents</p>
            <h1 className="text-lg font-semibold">{title}</h1>
          </div>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="email" className="label">
              Email
            </label>
            <input id="email" type="email" required autoComplete="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label htmlFor="password" className="label">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={8}
              autoComplete={submitLabel === 'Log in' ? 'current-password' : 'new-password'}
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {submitLabel !== 'Log in' && <p className="mt-1 text-xs text-slate-500">At least 8 characters.</p>}
          </div>
          <ErrorBox message={error} />
          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? 'Please wait…' : submitLabel}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-slate-500">
          {footer.text}{' '}
          <Link to={footer.to} className="font-medium text-indigo-600 hover:underline">
            {footer.linkText}
          </Link>
        </p>
      </div>
    </div>
  )
}
