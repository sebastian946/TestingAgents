import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../api'
import { Logo } from '../components/Logo'
import { ErrorBox } from '../components/ui'

interface Props {
  title: string
  subtitle: string
  submitLabel: string
  onSubmit: (email: string, password: string) => Promise<void>
  footer: { text: string; linkText: string; to: string }
}

/** The one form behind /login and /register: they differ only in the call they make. */
export function AuthForm({ title, subtitle, submitLabel, onSubmit, footer }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/dashboard'
  const isLogin = submitLabel === 'Log in'

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
    <div className="auth-shell">
      <div className="card auth-card">
        <div style={{ marginBottom: 'var(--space-6)' }}>
          <Logo to="/" />
        </div>
        <h1 className="card-title">{title}</h1>
        <p className="muted" style={{ margin: '0 0 var(--space-16)' }}>
          {subtitle}
        </p>
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="email" className="label">
              Email
            </label>
            <input id="email" type="email" required autoComplete="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="password" className="label">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={8}
              autoComplete={isLogin ? 'current-password' : 'new-password'}
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {isLogin ? (
              <div className="help" style={{ textAlign: 'right' }}>
                <Link to="/forgot-password">Forgot your password?</Link>
              </div>
            ) : (
              <div className="help">At least 8 characters.</div>
            )}
          </div>
          {error && (
            <div style={{ marginTop: 'var(--space-16)' }}>
              <ErrorBox message={error} />
            </div>
          )}
          <button type="submit" className="btn-primary" style={{ width: '100%', marginTop: 'var(--space-6)' }} disabled={busy}>
            {busy ? 'Please wait…' : submitLabel}
          </button>
        </form>
        <p className="help" style={{ textAlign: 'center', marginTop: 'var(--space-16)' }}>
          {footer.text} <Link to={footer.to}>{footer.linkText}</Link>
        </p>
        {!isLogin && (
          <p className="help" style={{ textAlign: 'center', marginTop: 'var(--space-2)' }}>
            By creating an account you agree to the <a href="/terms">Terms</a> and the <a href="/privacy">Privacy policy</a>.
          </p>
        )}
      </div>
    </div>
  )
}
