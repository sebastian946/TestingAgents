"""Structured inventory of what a page offers to a user (WTA-11).

This is the context the Designer (LLM) receives instead of raw HTML: forms with their
fields, visible buttons and the main navigation. Everything is extracted in ONE
``page.evaluate`` call that returns plain JSON, so Playwright never has to serialize DOM
nodes. Hidden elements, scripts and styles are ignored, and every list is capped so a
page stays around 2KB of text.
"""
from dataclasses import asdict, dataclass, field
from typing import Any

# Caps keep the output small and predictable for the LLM prompt
MAX_FORMS = 5
MAX_FIELDS_PER_FORM = 15
MAX_BUTTONS = 15
MAX_NAV_LINKS = 15
MAX_HEADINGS = 3
MAX_TEXT = 60  # characters per label/text


@dataclass
class FieldInfo:
    tag: str  # input | select | textarea
    type: str  # text, email, password, checkbox, ...
    name: str | None = None
    id: str | None = None  # fallback identifier: React-style forms often have ids but no names
    label: str | None = None  # <label for>, wrapping/sibling <label>, aria-label(ledby)
    placeholder: str | None = None
    required: bool = False
    options: list[str] = field(default_factory=list)  # for <select>, first few


@dataclass
class FormInfo:
    action: str | None
    method: str
    fields: list[FieldInfo]
    submit_text: str | None = None
    virtual: bool = False  # fields found outside any <form> (JS-driven login/search boxes)


@dataclass
class PageInfo:
    url: str
    title: str | None
    headings: list[str] = field(default_factory=list)
    forms: list[FormInfo] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    nav_links: list[dict[str, str]] = field(default_factory=list)  # {"text", "href"}
    # Cheap counters for page_type classification (WTA-13): price_count, link_count,
    # max_similar_links (largest group of same-site links sharing their first path segment),
    # word_count (visible words; listings have few words per link, articles have many),
    # repeated_button_max (same button text N times: 20x "Add to basket" is a listing),
    # stock_words (product vocabulary on the page: "in stock", "sku", "quantity"...)
    signals: dict[str, int] = field(default_factory=dict)

    @property
    def has_password_field(self) -> bool:
        return any(f.type == "password" for form in self.forms for f in form.fields)

    def to_dict(self) -> dict[str, Any]:
        """Compact dict for storage/prompting: empty values are dropped."""
        return _compact(asdict(self))

    @classmethod
    def from_evaluate(cls, url: str, title: str | None, raw: dict[str, Any]) -> "PageInfo":
        forms = [
            FormInfo(
                action=f.get("action"),
                method=f.get("method", "get"),
                submit_text=f.get("submit_text"),
                virtual=bool(f.get("virtual", False)),
                fields=[FieldInfo(**fld) for fld in f.get("fields", [])],
            )
            for f in raw.get("forms", [])
        ]
        return cls(
            url=url,
            title=title,
            headings=raw.get("headings", []),
            forms=forms,
            buttons=raw.get("buttons", []),
            nav_links=raw.get("nav_links", []),
            signals=raw.get("signals", {}),
        )


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items() if v not in (None, "", [], False)}
    if isinstance(value, list):
        return [_compact(v) for v in value]
    return value


# JavaScript run in the page. Returns JSON only (strings, numbers, booleans, lists).
EXTRACT_JS = f"""
() => {{
  const MAX_TEXT = {MAX_TEXT};
  const clean = (s) => (s || "").replace(/\\s+/g, " ").trim().slice(0, MAX_TEXT) || null;
  const visible = (el) => {{
    if (!el || el.closest("script, style, noscript, template, [hidden]")) return false;
    if (el.checkVisibility) return el.checkVisibility({{checkOpacity: true, checkVisibilityCSS: true}});
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }};
  const labelFor = (el) => {{
    const byId = el.getAttribute("aria-labelledby");
    if (byId) {{
      const t = byId.split(/\\s+/).map(i => document.getElementById(i)?.textContent).join(" ");
      if (clean(t)) return clean(t);
    }}
    if (el.id) {{
      const l = document.querySelector(`label[for="${{CSS.escape(el.id)}}"]`)
        || document.getElementById(el.id + "-label");  // React/Bootstrap convention
      if (l) return clean(l.textContent);
    }}
    const wrap = el.closest("label");
    if (wrap) return clean(wrap.textContent);
    const aria = clean(el.getAttribute("aria-label"));
    if (aria) return aria;
    // Sibling label in the same row/group (label without `for`): walk up 3 levels and
    // use the group's label only if the group holds exactly this one field
    let node = el.parentElement;
    for (let i = 0; i < 3 && node && node.tagName !== "FORM"; i++, node = node.parentElement) {{
      const label = node.querySelector("label");
      const fields = node.querySelectorAll("input:not([type=hidden]), select, textarea");
      if (label && fields.length === 1) return clean(label.textContent);
    }}
    return clean(el.getAttribute("title"));
  }};
  const buttonText = (b) =>
    clean(b.innerText) || clean(b.value) || clean(b.getAttribute("aria-label")) || clean(b.getAttribute("title"));

  // --- forms -----------------------------------------------------------------
  const forms = [];
  const formsWithFields = new Set();
  const collectFields = (elements) => {{
    const fields = [];
    for (const el of elements) {{
      const type = (el.getAttribute("type") || (el.tagName === "INPUT" ? "text" : el.tagName.toLowerCase())).toLowerCase();
      if (["hidden", "submit", "button", "reset", "image"].includes(type)) continue;
      if (!visible(el)) continue;
      if (fields.length >= {MAX_FIELDS_PER_FORM}) break;
      const f = {{
        tag: el.tagName.toLowerCase(),
        type,
        name: clean(el.name) || null,
        id: clean(el.id) || null,
        label: labelFor(el),
        placeholder: clean(el.placeholder) || null,
        required: el.required || el.getAttribute("aria-required") === "true",
        options: [],
      }};
      if (el.tagName === "SELECT") {{
        f.options = Array.from(el.options).slice(0, 5).map(o => clean(o.textContent)).filter(Boolean);
      }}
      fields.push(f);
    }}
    return fields;
  }};
  for (const form of Array.from(document.querySelectorAll("form")).slice(0, {MAX_FORMS})) {{
    const fields = collectFields(form.querySelectorAll("input, select, textarea"));
    if (!fields.length) continue;  // e.g. a search form that is hidden, or only hidden inputs
    formsWithFields.add(form);
    // explicit submit first; SPAs often wire a plain <button type="button"> instead
    const submit = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])')
      || form.querySelector("button");
    forms.push({{
      action: form.getAttribute("action") ? form.action : null,
      method: (form.getAttribute("method") || "get").toLowerCase(),
      submit_text: submit ? buttonText(submit) : null,
      fields,
    }});
  }}

  // --- buttons (outside forms, deduplicated by text) ---------------------------
  const buttons = [];
  const buttonCounts = {{}};  // same text repeated N times = a listing of cards, not a product
  for (const b of document.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"], a.btn, a[class*="button"]')) {{
    const owner = b.closest("form");
    if ((owner && formsWithFields.has(owner)) || !visible(b)) continue;
    const t = buttonText(b);
    if (!t) continue;
    buttonCounts[t] = (buttonCounts[t] || 0) + 1;
    if (!buttons.includes(t) && buttons.length < {MAX_BUTTONS}) buttons.push(t);
  }}
  const repeated_button_max = Math.max(0, ...Object.values(buttonCounts));

  // --- virtual form: visible fields outside any <form> (JS-driven login/search) ---
  if (forms.length < {MAX_FORMS}) {{
    const orphans = collectFields(
      Array.from(document.querySelectorAll("input, select, textarea")).filter(el => !el.closest("form")));
    if (orphans.length) {{
      const submit = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]'))
        .find(b => !b.closest("form") && visible(b));
      forms.push({{ action: null, method: "js", submit_text: submit ? buttonText(submit) : null, fields: orphans, virtual: true }});
    }}
  }}

  // --- main navigation -------------------------------------------------------
  const navScope = document.querySelector('nav, header, [role="navigation"]') || document.body;
  const nav_links = [];
  const seenHref = new Set();
  for (const a of navScope.querySelectorAll("a[href]")) {{
    if (!visible(a)) continue;
    const text = clean(a.innerText) || clean(a.getAttribute("aria-label"));
    const href = a.href;
    if (!text || !href || seenHref.has(href) || href.startsWith("javascript:")) continue;
    seenHref.add(href);
    nav_links.push({{ text, href }});
    if (nav_links.length >= {MAX_NAV_LINKS}) break;
  }}

  const headings = Array.from(document.querySelectorAll("h1, h2"))
    .filter(visible).map(h => clean(h.innerText)).filter(Boolean).slice(0, {MAX_HEADINGS});

  // --- signals for page_type classification ------------------------------------
  // prices: "$ 19.99", "19,99 €", "USD 20", "20 EUR" ... counted on the visible text
  const text = (document.body.innerText || "").slice(0, 200000);
  const priceRe = /(?:[$€£]\\s?\\d[\\d.,]*|\\d[\\d.,]*\\s?(?:€|£|USD|EUR|COP|MXN|GBP))/g;
  const price_count = (text.match(priceRe) || []).length;
  // product-detail vocabulary (stock, sku, quantity...) counted on the visible text
  const stockRe = /\\b(in stock|out of stock|availability|sku|quantity|qty|add to (cart|bag|basket)|en stock|agotado|disponible|cantidad)\\b/gi;
  const stock_words = (text.match(stockRe) || []).length;
  const word_count = text.split(/\\s+/).filter(Boolean).length;
  // similar links: /catalogue/a, /catalogue/b ... grouped by first path segment (depth >= 2)
  const groups = {{}};
  let link_count = 0;
  for (const a of document.querySelectorAll("a[href]")) {{
    let u; try {{ u = new URL(a.href); }} catch {{ continue; }}
    if (u.origin !== location.origin) continue;
    link_count++;
    const segs = u.pathname.split("/").filter(Boolean);
    if (segs.length >= 2) groups[segs[0]] = (groups[segs[0]] || 0) + 1;
  }}
  const max_similar_links = Math.max(0, ...Object.values(groups));
  const signals = {{ price_count, stock_words, link_count, max_similar_links, word_count, repeated_button_max }};

  return {{ forms, buttons, nav_links, headings, signals }};
}}
"""
