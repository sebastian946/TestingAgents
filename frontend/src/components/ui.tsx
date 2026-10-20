import type { ReactNode } from 'react'

import type { JobStatus } from '../api'

export function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={`badge badge-${status}`}>
      <span className="dot" />
      {status}
    </span>
  )
}

const PRIORITIES = new Set(['critical', 'high', 'medium', 'low'])

export function PriorityBadge({ priority }: { priority: string | null }) {
  if (!priority) return null
  const level = PRIORITIES.has(priority) ? priority : 'low'
  return (
    <span className={`badge badge-priority badge-${level}`}>
      <span className="dot" />
      {priority}
    </span>
  )
}

export type NoticeVariant = 'warning' | 'error' | 'success' | 'info'

const ICONS: Record<NoticeVariant, ReactNode> = {
  warning: (
    <svg viewBox="0 0 24 24">
      <path d="M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
    </svg>
  ),
  error: (
    <svg viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4m0 4h.01" />
    </svg>
  ),
  success: (
    <svg viewBox="0 0 24 24">
      <path d="M20 6L9 17l-5-5" />
    </svg>
  ),
  info: (
    <svg viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-4m0-4h.01" />
    </svg>
  ),
}

/** Inline message: VerifyBanner, errors, the "Exploring…" progress, confirmations. */
export function Notice({ variant, children, bar = false }: { variant: NoticeVariant; children: ReactNode; bar?: boolean }) {
  return (
    <div role={variant === 'error' ? 'alert' : 'status'} className={`notice notice-${variant}${bar ? ' notice-bar' : ''}`}>
      {ICONS[variant]}
      <span>{children}</span>
    </div>
  )
}

export function ErrorBox({ message, children }: { message: string | null; children?: ReactNode }) {
  if (!message) return null
  return (
    <Notice variant="error">
      {message}
      {children}
    </Notice>
  )
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function duration(start: string | null, end: string | null): string {
  if (!start) return '—'
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime()
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export const LANGUAGE_NAMES: Record<string, string> = { en: 'English', es: 'Español' }
