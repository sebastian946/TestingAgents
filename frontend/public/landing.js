// Bilingual switch for the landing: every string carries data-en / data-es. The choice is
// remembered per browser; the first visit follows the browser language. Kept in a file
// because the Content-Security-Policy forbids inline scripts.
(function () {
  var KEY = 'tests-wise.lang'
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
})()
