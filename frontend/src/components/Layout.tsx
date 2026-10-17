import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth'
import { VerifyBanner } from './VerifyBanner'

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-600 text-white">✓</span>
            Testing Agents
          </Link>
          <nav className="flex items-center gap-3 text-sm">
            <NavLink to="/" end className={({ isActive }) => (isActive ? 'font-medium text-indigo-600' : 'text-slate-600 dark:text-slate-300')}>
              Jobs
            </NavLink>
            <NavLink to="/new" className="btn-primary px-3 py-1.5">
              New job
            </NavLink>
            <span className="hidden text-slate-500 sm:inline" title={user?.email}>
              {user?.email}
            </span>
            <button
              type="button"
              className="text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
              onClick={() => {
                logout()
                navigate('/login')
              }}
            >
              Log out
            </button>
          </nav>
        </div>
      </header>
      <VerifyBanner />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
