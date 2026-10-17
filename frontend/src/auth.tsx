/**
 * Session state: the token lives in localStorage (api.ts) and the user in React state.
 * On load, a stored token is checked with GET /auth/me, so a token that expired while the
 * tab was closed sends the user to the login page instead of to a wall of 401s.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { api, getToken, onUnauthorized, setToken, type User } from './api'

interface AuthState {
  user: User | null
  /** true until the stored token has been checked */
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState<boolean>(() => getToken() !== null)

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  useEffect(() => {
    onUnauthorized.handler = logout
    return () => {
      onUnauthorized.handler = null
    }
  }, [logout])

  useEffect(() => {
    if (!getToken()) return
    let cancelled = false
    api
      .me()
      .then((me) => {
        if (!cancelled) setUser(me)
      })
      .catch(() => {
        if (!cancelled) logout()
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [logout])

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      async login(email, password) {
        const token = await api.login(email, password)
        setToken(token.access_token)
        setUser(token.user)
      },
      async register(email, password) {
        const token = await api.register(email, password)
        setToken(token.access_token)
        setUser(token.user)
      },
      logout,
    }),
    [user, loading, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

/** Wraps private routes: anonymous visitors go to /login and come back here afterwards. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <p className="p-8 text-center text-sm text-slate-500">Loading…</p>
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}
