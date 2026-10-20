import { useState } from 'react'

import { api, ApiError } from '../api'
import { useAuth } from '../auth'

/** Shown on every private page until the account's email is verified. */
export function VerifyBanner() {
  const { user } = useAuth()
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle')
  const [message, setMessage] = useState('')

  if (!user || user.email_verified) return null

  async function resend() {
    setState('sending')
    try {
      const result = await api.requestVerification()
      setMessage(result.detail)
      setState('sent')
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not send the email.')
      setState('error')
    }
  }

  return (
    <div className="notice notice-warning notice-bar" role="status">
      <svg viewBox="0 0 24 24">
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="M3 7l9 6 9-6" />
      </svg>
      <span>
        Confirm your email (<b>{user.email}</b>) to start creating jobs: check your inbox for the link.{' '}
        {state === 'sent' || state === 'error' ? (
          message
        ) : (
          <button type="button" className="link" onClick={resend} disabled={state === 'sending'}>
            {state === 'sending' ? 'Sending…' : 'Send the link again'}
          </button>
        )}
      </span>
    </div>
  )
}
