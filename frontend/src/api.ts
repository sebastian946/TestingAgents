/**
 * The API client: one fetch wrapper that adds the bearer token, parses errors and types
 * the responses. Paths are relative: in development Vite proxies them to :8000, in
 * production Caddy does (same origin, so no CORS). VITE_API_URL overrides that for a
 * frontend hosted elsewhere.
 */

export const API_URL = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? ''

const TOKEN_KEY = 'testing-agents.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* private mode: the session lasts while the tab is open */
  }
}

// ---------------------------------------------------------------- types (mirror models.py)

export type Language = 'en' | 'es'
export type JobStatus = 'queued' | 'running' | 'done' | 'failed'

export interface User {
  id: string
  email: string
  email_verified: boolean
  created_at: string
}

export interface Token {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface Job {
  id: string
  url: string
  description: string | null
  language: Language
  /** The login page when the site was explored logged in; credentials are never returned */
  login_url: string | null
  status: JobStatus
  pages_crawled: number
  total_scenarios: number
  report_path: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Page {
  id: number
  job_id: string
  url: string
  title: string | null
  page_type: string | null
  elements: Record<string, unknown> | null
  created_at: string
}

export interface Scenario {
  id: number
  job_id: string
  page_id: number
  scenario_code: string
  title: string
  steps: string[]
  expected_result: string
  priority: string | null
  category: string | null
}

export interface Usage {
  job_id: string
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  cost_usd: number
  calls_without_price: number
  by_model: { model: string; calls: number; cost_usd: number }[]
}

export interface JobLogin {
  login_url?: string
  username: string
  password: string
  authorized: boolean
}

export interface JobCreate {
  url: string
  description?: string
  language: Language
  /** Log in before exploring (WTA-29): used once for this job, then deleted server-side */
  login?: JobLogin
}

// ---------------------------------------------------------------- errors

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

/** FastAPI puts the human message in `detail`: a string, an object with `message`, or a 422 list. */
function messageFrom(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  if (Array.isArray(detail)) {
    return detail.map((e: { loc?: unknown[]; msg?: string }) => `${e.loc?.at(-1) ?? 'field'}: ${e.msg ?? 'invalid'}`).join('; ')
  }
  if (status === 401) return 'Your session has expired. Log in again.'
  return `Request failed (${status}).`
}

export const onUnauthorized: { handler: (() => void) | null } = { handler: null }

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new ApiError(0, 'Cannot reach the API. Is it running?')
  }

  if (response.status === 204) return undefined as T
  const text = await response.text()
  let parsed: unknown = null
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = null
  }
  if (!response.ok) {
    // An expired or revoked token on a private route: drop the session (login and register
    // answer 401 for a wrong password, which is not a session problem)
    if (response.status === 401 && !path.startsWith('/auth/login') && !path.startsWith('/auth/register')) {
      onUnauthorized.handler?.()
    }
    throw new ApiError(response.status, messageFrom(response.status, parsed), (parsed as { detail?: unknown } | null)?.detail)
  }
  return parsed as T
}

// ---------------------------------------------------------------- endpoints

export const api = {
  register: (email: string, password: string) => request<Token>('POST', '/auth/register', { email, password }),
  login: (email: string, password: string) => request<Token>('POST', '/auth/login', { email, password }),
  me: () => request<User>('GET', '/auth/me'),
  requestVerification: () => request<{ detail: string }>('POST', '/auth/verify/request'),
  verifyEmail: (token: string) => request<User>('POST', '/auth/verify', { token }),
  forgotPassword: (email: string) => request<{ detail: string }>('POST', '/auth/password/forgot', { email }),
  resetPassword: (token: string, password: string) => request<Token>('POST', '/auth/password/reset', { token, password }),

  listJobs: (limit = 50) => request<Job[]>('GET', `/jobs?limit=${limit}`),
  createJob: (payload: JobCreate) => request<Job>('POST', '/jobs', payload),
  getJob: (id: string) => request<Job>('GET', `/jobs/${id}`),
  getPages: (id: string) => request<Page[]>('GET', `/jobs/${id}/pages`),
  getScenarios: (id: string) => request<Scenario[]>('GET', `/jobs/${id}/scenarios`),
  getUsage: (id: string) => request<Usage>('GET', `/jobs/${id}/usage`),
  deleteJob: (id: string) => request<void>('DELETE', `/jobs/${id}`),

  /** The report needs the bearer header, so a plain link cannot download it: fetch the bytes
   *  and hand the browser a blob with the server's file name. */
  async downloadReport(id: string): Promise<void> {
    const token = getToken()
    const response = await fetch(`${API_URL}/jobs/${id}/report`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!response.ok) {
      let parsed: unknown = null
      try {
        parsed = await response.json()
      } catch {
        /* not JSON */
      }
      throw new ApiError(response.status, messageFrom(response.status, parsed))
    }
    const match = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = match?.[1] ?? `test-report-${id}.xlsx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  },
}
