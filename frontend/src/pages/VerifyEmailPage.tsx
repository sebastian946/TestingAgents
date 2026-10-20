import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api, ApiError, getToken } from '../api'
import { useAuth } from '../auth'
import { Logo } from '../components/Logo'

/** /verify?token=…: the link from the verification email. Works logged in or not. */
export function VerifyEmailPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const { user, refresh } = useAuth()
  const [state, setState] = useState<'working' | 'ok' | 'error'>('working')
  const [message, setMessage] = useState('')
  // The token is single-use: send it exactly once, even when React StrictMode (development)
  // or a double click mounts the effect twice
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    if (!token) {
      setState('error')
      setMessage('This link is missing its token.')
      return
    }
    api
      .verifyEmail(token)
      .then(async () => {
        // The page may have been opened fresh from the email: `user` is not loaded yet, but a
        // stored token means a session exists and its email_verified flag is now stale
        if (getToken()) await refresh().catch(() => undefined)
        setState('ok')
      })
      .catch((err) => {
        setState('error')
        setMessage(err instanceof ApiError ? err.message : 'Could not verify the email.')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  return (
    <div className="auth-shell">
      <div className={`card auth-card${state === 'ok' ? ' card-soft' : ''}`} style={{ textAlign: 'center' }}>
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 'var(--space-6)' }}>
          <Logo to="/" />
        </div>
        {state === 'working' && <p className="muted">Verifying your email…</p>}
        {state === 'ok' && (
          <>
            <h1 className="card-title">Email verified</h1>
            <p style={{ margin: '0 0 var(--space-6)' }}>You're all set. Create your first job and we'll start exploring.</p>
            <Link to={user ? '/new' : '/login'} className="btn-primary">
              {user ? 'Create a job' : 'Log in'}
            </Link>
          </>
        )}
        {state === 'error' && (
          <>
            <h1 className="card-title">Link not valid</h1>
            <p className="muted" style={{ margin: '0 0 var(--space-6)' }}>
              {message}
            </p>
            <Link to={user ? '/dashboard' : '/login'} className="btn-secondary">
              {user ? 'Request a new link from the dashboard' : 'Log in'}
            </Link>
          </>
        )}
      </div>
    </div>
  )
}
