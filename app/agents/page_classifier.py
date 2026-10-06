"""Heuristic page_type classification (WTA-13).

Deterministic rules over the ``PageInfo`` inventory plus a few cheap counters
(``PageInfo.signals``) — no LLM. A login page is tested very differently from a blog post,
so the Designer uses this label to ask for the right kind of scenarios. Heuristics are
cheaper, faster and good enough for the MVP; they also make results reproducible.

Rules, evaluated in this order (first match wins):

1. ``login``    – a form has a ``password`` field and looks like sign-in: at most 3 fields,
                  or the submit/headings/URL say "log in / sign in / iniciar sesión".
2. ``signup``   – a form has a ``password`` field but more than 3 fields, a confirm-password
                  field, or wording like "sign up / register / create account".
3. ``checkout`` – card fields (card number, cvv, expiry) or wording like "checkout / pay /
                  place order / finalizar compra" in buttons, headings or the URL.
4. ``product``  – a few prices (1 to 5) AND either a buy/add-to-cart button/link or product
                  vocabulary ("in stock", "availability", "sku", "quantity"), and no button
                  repeated >= 4 times (that would be a grid of product cards).
5. ``listing``  – many prices (>= 6), or the same button text >= 4 times, or many links
                  sharing the same first path segment at depth >= 2 (``/catalogue/a``,
                  ``/catalogue/b``, ... >= 6) on a page with little text per link
                  (< 6 words/link; real listings measure ~3, a Wikipedia article ~12),
                  or a URL path like /products, /catalogue, /category, /blog, /search, /news.
6. ``form``     – a form with at least 2 visible fields that is not just a search box.
7. ``content``  – anything else (home pages, articles, about pages, search-only pages).

The thresholds are constants below so they can be tuned without touching the logic.
"""
import enum
import re
from urllib.parse import urlsplit

from agents.page_info import FormInfo, PageInfo

LOGIN_MAX_FIELDS = 3
LISTING_MIN_PRICES = 6  # a product page shows a few (price, tax, related items); a listing shows many
LISTING_MIN_SIMILAR_LINKS = 6
LISTING_MAX_WORDS_PER_LINK = 6
LISTING_MIN_REPEATED_BUTTONS = 4

LOGIN_WORDS = re.compile(r"\b(log ?in|sign ?in|iniciar sesi[oó]n|ingresar|acceder)\b", re.I)
SIGNUP_WORDS = re.compile(r"\b(sign ?up|register|registr\w*|create (an )?account|crear (una )?cuenta)\b", re.I)
CHECKOUT_WORDS = re.compile(
    r"\b(checkout|place (your )?order|pay now|payment|proceed to pay|finalizar compra|pagar|realizar pedido)\b", re.I
)
CART_WORDS = re.compile(
    r"\b(add to (cart|bag|basket)|buy( now)?|purchase|comprar|a[ñn]adir al carrito|agregar al carrito)\b", re.I
)
CARD_FIELD = re.compile(r"(card|cc[-_]?num|cvv|cvc|expir|exp[-_]?(month|year|date))", re.I)
SEARCH_FIELD = re.compile(r"^(q|s|query|search|busca\w*|keyword)$", re.I)
LISTING_PATHS = re.compile(
    r"^/(products?|catalog(ue)?|categor(y|ies)|shop|store|collections?|blog|news|articles?|search|tag|tags)(/|$)", re.I
)


class PageType(str, enum.Enum):
    LOGIN = "login"
    SIGNUP = "signup"
    CHECKOUT = "checkout"
    PRODUCT = "product"
    LISTING = "listing"
    FORM = "form"
    CONTENT = "content"


def _field_types(form: FormInfo) -> list[str]:
    return [f.type for f in form.fields]


def _field_names(form: FormInfo) -> list[str]:
    return [f.name or f.id or "" for f in form.fields]


def _is_search_form(form: FormInfo) -> bool:
    """A single search box (type=search or named q/search) is not a 'form' page."""
    if len(form.fields) != 1:
        return False
    only = form.fields[0]
    return only.type == "search" or bool(SEARCH_FIELD.match(only.name or only.id or ""))


def _wording(info: PageInfo, form: FormInfo | None = None) -> str:
    """Text where intent words are looked for: submit text, buttons, headings, title, path."""
    parts = [*(info.headings), *(info.buttons), info.title or "", urlsplit(info.url).path]
    if form is not None:
        parts.append(form.submit_text or "")
    return " ".join(parts)


def classify_page(info: PageInfo) -> PageType:
    """Return the PageType for a crawled page. Pure function: no I/O, no randomness."""
    signals = info.signals or {}
    price_count = int(signals.get("price_count", 0))
    similar_links = int(signals.get("max_similar_links", 0))
    words_per_link = int(signals.get("word_count", 0)) / max(int(signals.get("link_count", 0)), 1)
    repeated_buttons = int(signals.get("repeated_button_max", 0))
    stock_words = int(signals.get("stock_words", 0))
    path = urlsplit(info.url).path or "/"
    all_text = _wording(info)
    for form in info.forms:
        all_text += " " + (form.submit_text or "")

    # 1 & 2: password-based forms
    for form in info.forms:
        types = _field_types(form)
        if "password" not in types:
            continue
        text = _wording(info, form)
        confirm = types.count("password") >= 2
        if confirm or len(form.fields) > LOGIN_MAX_FIELDS or SIGNUP_WORDS.search(text):
            if not LOGIN_WORDS.search(form.submit_text or "") or confirm:
                return PageType.SIGNUP
        return PageType.LOGIN

    # 3: checkout
    for form in info.forms:
        if any(CARD_FIELD.search(n) for n in _field_names(form)):
            return PageType.CHECKOUT
    if CHECKOUT_WORDS.search(all_text):
        return PageType.CHECKOUT

    # 4: product
    few_prices = 1 <= price_count < LISTING_MIN_PRICES
    product_intent = bool(CART_WORDS.search(all_text)) or stock_words >= 1
    if few_prices and product_intent and repeated_buttons < LISTING_MIN_REPEATED_BUTTONS:
        return PageType.PRODUCT

    # 5: listing
    link_heavy = similar_links >= LISTING_MIN_SIMILAR_LINKS and words_per_link < LISTING_MAX_WORDS_PER_LINK
    card_grid = repeated_buttons >= LISTING_MIN_REPEATED_BUTTONS
    if price_count >= LISTING_MIN_PRICES or card_grid or link_heavy or LISTING_PATHS.match(path):
        return PageType.LISTING

    # 6: form
    if any(len(form.fields) >= 2 and not _is_search_form(form) for form in info.forms):
        return PageType.FORM

    return PageType.CONTENT
