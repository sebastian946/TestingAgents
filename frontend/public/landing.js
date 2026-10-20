// Landing behaviour, in a file because the Content-Security-Policy forbids inline scripts:
// 1. bilingual switch: every string carries data-en / data-es; the choice is remembered per
//    browser and the first visit follows the browser language;
// 2. session awareness: when the SPA stored a token (same localStorage key as src/api.ts),
//    the header shows the account instead of "Log in" and the plan buttons go to /billing.
(function () {
  var KEY = 'tests-wise.lang'
  var TOKEN_KEY = 'testing-agents.token'

  function setLang(l) {
    document.documentElement.lang = l
    document.querySelectorAll('[data-en]').forEach(function (e) {
      e.textContent = e.getAttribute('data-' + l) || e.getAttribute('data-en')
    })
    document.querySelectorAll('.lang button').forEach(function (b) {
      b.classList.toggle('on', b.dataset.l === l)
      b.setAttribute('aria-pressed', b.dataset.l === l ? 'true' : 'false')
    })
    try {
      localStorage.setItem(KEY, l)
    } catch (_) {
      /* private mode */
    }
  }
  document.querySelectorAll('.lang button').forEach(function (b) {
    b.addEventListener('click', function () {
      setLang(b.dataset.l)
    })
  })
  var saved = null
  try {
    saved = localStorage.getItem(KEY)
  } catch (_) {
    /* private mode */
  }
  var browser = (navigator.language || 'en').toLowerCase().indexOf('es') === 0 ? 'es' : 'en'
  setLang(saved === 'es' || saved === 'en' ? saved : browser)

  // Session: ask the API who the token belongs to. A stale token answers 401 and the page
  // simply keeps the anonymous header
  var token = null
  try {
    token = localStorage.getItem(TOKEN_KEY)
  } catch (_) {
    /* private mode */
  }
  if (!token) return
  fetch('/auth/me', { headers: { Authorization: 'Bearer ' + token } })
    .then(function (r) {
      return r.ok ? r.json() : null
    })
    .then(function (me) {
      if (!me || !me.email) return
      document.querySelectorAll('[data-anon]').forEach(function (e) {
        e.hidden = true
      })
      document.querySelectorAll('[data-auth]').forEach(function (e) {
        e.hidden = false
      })
      var who = document.querySelector('[data-auth-email]')
      if (who) who.textContent = me.email
      var initials = document.querySelector('[data-auth-initials]')
      if (initials) initials.textContent = me.email.slice(0, 2).toUpperCase()
      // Plan buttons: a logged-in visitor goes straight to the plan page, which opens the
      // checkout for ?plan=; the free plan just opens the dashboard
      document.querySelectorAll('[data-plan]').forEach(function (a) {
        var plan = a.getAttribute('data-plan')
        a.setAttribute('href', plan === 'free' ? '/dashboard' : '/billing?plan=' + plan)
      })
      document.querySelectorAll('[data-start]').forEach(function (a) {
        a.setAttribute('href', '/new')
      })
    })
    .catch(function () {
      /* offline: keep the anonymous header */
    })
})()
