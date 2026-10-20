import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth'
import { Logo } from './Logo'
import { VerifyBanner } from './VerifyBanner'

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header className={`header${open ? ' is-open' : ''}`}>
        <div className="header-inner">
          <Logo size={28} />
          <nav className="nav" aria-label="Main">
            <NavLink to="/dashboard" end className={({ isActive }) => (isActive ? 'is-active' : undefined)} onClick={() => setOpen(false)}>
              Jobs
            </NavLink>
            <NavLink to="/billing" className={({ isActive }) => (isActive ? 'is-active' : undefined)} onClick={() => setOpen(false)}>
              Plan
            </NavLink>
          </nav>
          <span className="spacer" />
          <NavLink to="/new" className="btn-primary btn-sm">
            <svg viewBox="0 0 24 24">
              <path d="M12 5v14M5 12h14" />
            </svg>
            New job
          </NavLink>
          <NavLink to="/account" className="user-email" title="Account settings" onClick={() => setOpen(false)}>
            <span className="avatar" aria-hidden="true">
              {(user?.email ?? '?').slice(0, 2).toUpperCase()}
            </span>
            <span className="text">{user?.email}</span>
          </NavLink>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() => {
              logout()
              navigate('/login')
            }}
          >
            Log out
          </button>
          <button type="button" className="btn-ghost btn-sm menu-toggle" aria-expanded={open} aria-label="Menu" onClick={() => setOpen((v) => !v)}>
            <svg viewBox="0 0 24 24">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
        </div>
      </header>
      <VerifyBanner />
      <main className="page" style={{ flex: 1, width: '100%' }}>
        <Outlet />
      </main>
      <footer className="site-footer">
        <span>© 2026 Tests Wise</span>
        <span className="spacer" />
        <a href="/terms">Terms</a>
        <a href="/privacy">Privacy</a>
        <a href="mailto:hola@tests-wise.com">hola@tests-wise.com</a>
      </footer>
    </div>
  )
}
