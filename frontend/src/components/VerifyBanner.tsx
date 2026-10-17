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
    <div className="border-b border-amber-200 bg-amber-50 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-2">
        <span>
          Confirm your email (<b>{user.email}</b>) to start creating jobs: check your inbox for the link.
        </span>
        {state === 'sent' || state === 'error' ? (
          <span>{message}</span>
        ) : (
          <button type="button" className="font-medium underline" onClick={resend} disabled={state === 'sending'}>
            {state === 'sending' ? 'Sending…' : 'Send the link again'}
          </button>
        )}
      </div>
    </div>
  )
}
