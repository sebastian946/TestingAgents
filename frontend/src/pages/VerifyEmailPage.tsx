import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api, ApiError } from '../api'
import { useAuth } from '../auth'

/** /verify?token=…: the link from the verification email. Works logged in or not. */
export function VerifyEmailPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const { user, refresh } = useAuth()
  const [state, setState] = useState<'working' | 'ok' | 'error'>('working')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!token) {
      setState('error')
      setMessage('This link is missing its token.')
      return
    }
    let cancelled = false
    api
      .verifyEmail(token)
      .then(async () => {
        if (user) await refresh()
        if (!cancelled) setState('ok')
      })
      .catch((err) => {
        if (cancelled) return
        setState('error')
        setMessage(err instanceof ApiError ? err.message : 'Could not verify the email.')
      })
    return () => {
      cancelled = true
    }
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
