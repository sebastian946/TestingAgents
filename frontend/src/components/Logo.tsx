import { Link } from 'react-router-dom'

/** The Tests Wise mark: a brand square, an on-brand check and an accent spark. Inline SVG so
 *  the fills follow the theme; /favicon.svg is the same mark with fixed light colors. */
export function Mark({ size = 32 }: { size?: number }) {
  return (
    <svg viewBox="0 0 40 40" width={size} height={size} aria-hidden="true">
      <rect width="40" height="40" rx="10" style={{ fill: 'var(--brand)' }} />
      <path d="M11 21.5l6.5 6.5L29 15" fill="none" style={{ stroke: 'var(--on-brand)' }} strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="31" cy="9.5" r="4" style={{ fill: 'var(--accent)' }} />
    </svg>
  )
}

export function Logo({ to = '/dashboard', size = 32, wordmarkSize }: { to?: string; size?: number; wordmarkSize?: number }) {
  return (
    <Link to={to} className="logo" aria-label="Tests Wise">
      <Mark size={size} />
      <span className="wordmark" style={wordmarkSize ? { fontSize: wordmarkSize, lineHeight: `${wordmarkSize + 4}px` } : undefined}>
        Tests Wise
      </span>
    </Link>
  )
}
