import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api, ApiError, getToken } from '../api'
import { useAuth } from '../auth'

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
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="card w-full max-w-sm p-6 text-center">
        {state === 'working' && <p className="text-sm text-slate-500">Verifying your email…</p>}
        {state === 'ok' && (
          <>
            <p className="text-2xl">✅</p>
            <h1 className="mt-2 text-lg font-semibold">Email verified</h1>
            <p className="mt-1 text-sm text-slate-500">You can create jobs now.</p>
            <Link to={user ? '/new' : '/login'} className="btn-primary mt-4">
              {user ? 'Create a job' : 'Log in'}
            </Link>
          </>
        )}
        {state === 'error' && (
          <>
            <h1 className="text-lg font-semibold">Link not valid</h1>
            <p className="mt-1 text-sm text-slate-500">{message}</p>
            <Link to={user ? '/' : '/login'} className="btn-secondary mt-4">
              {user ? 'Request a new link from the dashboard' : 'Log in'}
            </Link>
          </>
        )}
      </div>
    </div>
  )
}
