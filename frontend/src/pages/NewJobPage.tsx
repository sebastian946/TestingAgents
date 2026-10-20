import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, ApiError, type Language } from '../api'
import { ErrorBox } from '../components/ui'

export function NewJobPage() {
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [language, setLanguage] = useState<Language>('en')
  const [withLogin, setWithLogin] = useState(false)
  const [loginUrl, setLoginUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [authorized, setAuthorized] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const job = await api.createJob({
        url: url.trim(),
        description: description.trim() || undefined,
        language,
        login: withLogin ? { login_url: loginUrl.trim() || undefined, username, password, authorized } : undefined,
      })
      navigate(`/job/${job.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the job.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <h1 className="text-xl font-semibold">New job</h1>
      <form onSubmit={handleSubmit} className="card space-y-5 p-6">
        <div>
          <label htmlFor="url" className="label">
            Site URL
          </label>
          <input
            id="url"
            type="url"
            required
            placeholder="https://www.example.com/"
            className="input"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            autoFocus
          />
          <p className="mt-1 text-xs text-slate-500">Public http(s) site. Up to 8 pages of the same domain are explored.</p>
        </div>
        <div>
          <label htmlFor="description" className="label">
            What does the app do? <span className="font-normal text-slate-400">(optional, recommended)</span>
          </label>
          <textarea
            id="description"
            rows={3}
            maxLength={500}
            placeholder="Online clothing store. Login is the entry point; the checkout is the most important flow."
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <p className="mt-1 text-xs text-slate-500">The designer uses it to decide what matters most. {description.length}/500</p>
        </div>
        <fieldset>
          <legend className="label">Language of the scenarios and the report</legend>
          <div className="flex gap-4 text-sm">
            {(['en', 'es'] as Language[]).map((code) => (
              <label key={code} className="flex items-center gap-2">
                <input type="radio" name="language" value={code} checked={language === code} onChange={() => setLanguage(code)} />
                {code === 'en' ? 'English' : 'Español'}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="rounded-md border border-slate-200 p-4 dark:border-slate-700">
          <legend className="px-1 text-sm font-medium">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={withLogin} onChange={(e) => setWithLogin(e.target.checked)} />
              This site requires logging in
            </label>
          </legend>
          {withLogin && (
            <div className="space-y-3 pt-2">
              <div className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
                Use a <b>test account</b> on a <b>test environment</b>, never an admin or a real customer account: the
                explorer browses as this user. Action links (log out, delete, checkout, pay) are not followed, but we
                cannot guarantee how every site behaves. The credentials are encrypted, used only for this job and
                deleted as soon as the exploration ends.
              </div>
              <div>
                <label htmlFor="login_url" className="label">
                  Login page <span className="font-normal text-slate-400">(optional, defaults to the site URL)</span>
                </label>
                <input id="login_url" type="url" placeholder="https://www.example.com/login" className="input" value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="username" className="label">
                    Username or email
                  </label>
                  <input id="username" required={withLogin} autoComplete="off" className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
                </div>
                <div>
                  <label htmlFor="login_password" className="label">
                    Password
                  </label>
                  <input id="login_password" type="password" required={withLogin} autoComplete="off" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
              </div>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" required={withLogin} checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} className="mt-1" />
                <span>I confirm I am authorized to test this site with this account.</span>
              </label>
            </div>
          )}
        </fieldset>
        <ErrorBox message={error} />
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={() => navigate(-1)} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={busy}>
            {busy ? 'Starting…' : 'Start testing'}
          </button>
        </div>
      </form>
    </div>
  )
}
