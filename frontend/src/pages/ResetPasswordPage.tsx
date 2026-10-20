import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError } from '../api'
import { useAuth } from '../auth'
import { Logo } from '../components/Logo'
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
      navigate('/dashboard', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reset the password.')
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
        <h1 className="card-title">Choose a new password</h1>
        {!token ? (
          <p className="muted">This link is missing its token. Request a new one from the log in page.</p>
        ) : (
          <form onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="password" className="label">
                New password
              </label>
              <input id="password" type="password" required minLength={8} autoComplete="new-password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
              <div className="help">At least 8 characters.</div>
            </div>
            <div className="field">
              <label htmlFor="confirm" className="label">
                Repeat it
              </label>
              <input id="confirm" type="password" required minLength={8} autoComplete="new-password" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </div>
            {error && (
              <div style={{ marginTop: 'var(--space-4)' }}>
                <ErrorBox message={error} />
              </div>
            )}
            <button type="submit" className="btn-primary" style={{ width: '100%', marginTop: 'var(--space-6)' }} disabled={busy}>
              {busy ? 'Saving…' : 'Save and log in'}
            </button>
          </form>
        )}
        <p className="help" style={{ textAlign: 'center', marginTop: 'var(--space-4)' }}>
          <Link to="/login">Back to log in</Link>
        </p>
      </div>
    </div>
  )
}
