import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api, ApiError, type Language } from '../api'
import { ErrorBox, Notice } from '../components/ui'

export function NewJobPage() {
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [language, setLanguage] = useState<Language>('en')
  const [withLogin, setWithLogin] = useState(false)
  const [loginUrl, setLoginUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [authorized, setAuthorized] = useState(false)
  const [selectPages, setSelectPages] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [quotaExceeded, setQuotaExceeded] = useState(false)
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
        select_pages: selectPages,
      })
      navigate(`/job/${job.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the job.')
      setQuotaExceeded(err instanceof ApiError && err.status === 402)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ maxWidth: '36rem', margin: '0 auto' }}>
      <div className="page-head">
        <h1 className="page-title">New job</h1>
      </div>
      <form onSubmit={handleSubmit} className="card">
        <div className="field">
          <label htmlFor="url" className="label">
            Site URL
          </label>
          <input id="url" type="url" required placeholder="https://www.example.com/" className="input" value={url} onChange={(e) => setUrl(e.target.value)} autoFocus />
          <div className="help">Public http(s) site. Up to 8 pages of the same domain are explored.</div>
        </div>
        <div className="field">
          <label htmlFor="description" className="label">
            What does the app do? <span className="muted" style={{ fontWeight: 400 }}>(optional, recommended)</span>
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
          <div className="help" style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>The designer uses it to decide what matters most.</span>
            <span className="counter">{description.length}/500</span>
          </div>
        </div>
        <fieldset className="field" style={{ border: 0, padding: 0, margin: 'var(--space-4) 0 0' }}>
          <legend className="label">Language of the scenarios and the report</legend>
          <div className="row" style={{ gap: 'var(--space-4)' }}>
            {(['en', 'es'] as Language[]).map((code) => (
              <label key={code} className="radio">
                <input type="radio" name="language" value={code} checked={language === code} onChange={() => setLanguage(code)} />
                {code === 'en' ? 'English' : 'Español'}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="field" style={{ marginTop: 'var(--space-6)' }}>
          <label className="check" style={{ alignItems: 'flex-start' }}>
            <input type="checkbox" checked={selectPages} onChange={(e) => setSelectPages(e.target.checked)} style={{ marginTop: 2 }} />
            <span>
              <b>Let me choose the pages first.</b>
              <span className="help" style={{ display: 'block', marginTop: 2 }}>
                We explore up to 20 pages and pause; you pick which ones get scenarios. Only the pages you keep count toward the
                cost.
              </span>
            </span>
          </label>
        </div>

        <fieldset className="field" style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4)', margin: 'var(--space-6) 0 0' }}>
          <legend style={{ padding: '0 var(--space-1)' }}>
            <label className="check" style={{ fontWeight: 600 }}>
              <input type="checkbox" checked={withLogin} onChange={(e) => setWithLogin(e.target.checked)} />
              This site requires logging in
            </label>
          </legend>
          {withLogin && (
            <div className="stack" style={{ paddingTop: 'var(--space-2)' }}>
              <Notice variant="warning">
                Use a <b>test account</b> on a <b>test environment</b>, never an admin or a real customer account: the explorer browses as this
                user. Action links (log out, delete, checkout, pay) are not followed, but we cannot guarantee how every site behaves. The
                credentials are encrypted, used only for this job and deleted as soon as the exploration ends.
              </Notice>
              <div className="field">
                <label htmlFor="login_url" className="label">
                  Login page <span className="muted" style={{ fontWeight: 400 }}>(optional, defaults to the site URL)</span>
                </label>
                <input id="login_url" type="url" placeholder="https://www.example.com/login" className="input" value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} />
              </div>
              <div className="field-grid">
                <div className="field">
                  <label htmlFor="username" className="label">
                    Username or email
                  </label>
                  <input id="username" required={withLogin} autoComplete="off" className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="login_password" className="label">
                    Password
                  </label>
                  <input id="login_password" type="password" required={withLogin} autoComplete="off" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
              </div>
              <label className="check" style={{ alignItems: 'flex-start' }}>
                <input type="checkbox" required={withLogin} checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} style={{ marginTop: 2 }} />
                <span>I confirm I am authorized to test this site with this account.</span>
              </label>
            </div>
          )}
        </fieldset>

        {error && (
          <div style={{ marginTop: 'var(--space-6)' }}>
            <ErrorBox message={error}>{quotaExceeded && <> <Link to="/billing">See plans and upgrade</Link></>}</ErrorBox>
          </div>
        )}
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 'var(--space-6)' }}>
          <button type="button" className="btn-ghost" onClick={() => navigate(-1)} disabled={busy}>
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
