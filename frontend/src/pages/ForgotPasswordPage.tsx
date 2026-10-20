import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../api'
import { Logo } from '../components/Logo'
import { ErrorBox, Notice } from '../components/ui'

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
    <div className="auth-shell">
      <div className="card auth-card">
        <div style={{ marginBottom: 'var(--space-6)' }}>
          <Logo to="/" />
        </div>
        <h1 className="card-title">Reset your password</h1>
        {sent ? (
          <Notice variant="success">
            If <b>{email}</b> has an account, a reset link is on its way. It works for a limited time.
          </Notice>
        ) : (
          <form onSubmit={handleSubmit}>
            <p className="muted" style={{ margin: '0 0 var(--space-16)' }}>
              Enter your email and we will send you a link to choose a new password.
            </p>
            <div className="field">
              <label htmlFor="email" className="label">
                Email
              </label>
              <input id="email" type="email" required autoComplete="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            {error && (
              <div style={{ marginTop: 'var(--space-16)' }}>
                <ErrorBox message={error} />
              </div>
            )}
            <button type="submit" className="btn-primary" style={{ width: '100%', marginTop: 'var(--space-6)' }} disabled={busy}>
              {busy ? 'Sending…' : 'Send reset link'}
            </button>
          </form>
        )}
        <p className="help" style={{ textAlign: 'center', marginTop: 'var(--space-16)' }}>
          <Link to="/login">Back to log in</Link>
        </p>
      </div>
    </div>
  )
}
