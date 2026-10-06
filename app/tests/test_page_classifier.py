"""Unit tests for the page_type rules (WTA-13). Pure Python: no browser, no DB."""
import pytest

from agents.page_classifier import PageType, classify_page
from agents.page_info import FieldInfo, FormInfo, PageInfo


def _field(type_="text", name=None, **kw) -> FieldInfo:
    return FieldInfo(tag="input", type=type_, name=name, **kw)


def _page(url="https://shop.example/page", forms=(), buttons=(), headings=(), **signals) -> PageInfo:
    return PageInfo(url=url, title="t", forms=list(forms), buttons=list(buttons), headings=list(headings), signals=signals)


def test_login_page():
    form = FormInfo(action=None, method="post", submit_text="Log in",
                    fields=[_field(name="username"), _field("password", name="password")])
    assert classify_page(_page(forms=[form])) is PageType.LOGIN


def test_signup_page_by_confirm_password_and_by_wording():
    confirm = FormInfo(action=None, method="post", submit_text="Continue",
                       fields=[_field("email"), _field("password"), _field("password", name="confirm")])
    assert classify_page(_page(forms=[confirm])) is PageType.SIGNUP
    wording = FormInfo(action=None, method="post", submit_text="Create account",
                       fields=[_field("email"), _field("password")])
    assert classify_page(_page(forms=[wording])) is PageType.SIGNUP


def test_contact_form_is_form():
    form = FormInfo(action="/send", method="post", submit_text="Send message",
                    fields=[_field(name="name"), _field("email", name="email"), FieldInfo(tag="textarea", type="textarea", name="message")])
    assert classify_page(_page(url="https://a.example/contact", forms=[form])) is PageType.FORM


def test_search_box_only_is_content_not_form():
    search = FormInfo(action="/search", method="get", fields=[_field("search", name="q")])
    assert classify_page(_page(url="https://a.example/about", forms=[search])) is PageType.CONTENT


def test_product_page_needs_price_and_cart_button():
    assert classify_page(_page(url="https://a.example/item/42", buttons=["Add to cart"], price_count=1)) is PageType.PRODUCT
    assert classify_page(_page(url="https://a.example/item/42", buttons=["Add to cart"], price_count=0)) is PageType.CONTENT
    # product detail without any button (price + "In stock"), as on scraping sandboxes
    assert classify_page(_page(url="https://a.example/catalogue/item_1/index.html", price_count=4, stock_words=2)) is PageType.PRODUCT
    # twenty "Add to cart" buttons = a grid of product cards, i.e. a listing
    assert classify_page(_page(url="https://a.example/x", buttons=["Add to cart"], price_count=4, repeated_button_max=20)) is PageType.LISTING


def test_listing_by_prices_by_similar_links_and_by_path():
    assert classify_page(_page(url="https://a.example/x", price_count=12)) is PageType.LISTING
    assert classify_page(_page(url="https://a.example/x", max_similar_links=20, link_count=20, word_count=50)) is PageType.LISTING
    # an article with hundreds of wiki links but lots of text is content, not a listing
    assert classify_page(_page(url="https://a.example/x", max_similar_links=500, link_count=580, word_count=9000)) is PageType.CONTENT
    assert classify_page(_page(url="https://a.example/blog")) is PageType.LISTING


def test_checkout_by_card_fields_and_by_wording():
    card = FormInfo(action=None, method="post", fields=[_field(name="card_number"), _field(name="cvv")])
    assert classify_page(_page(forms=[card])) is PageType.CHECKOUT
    assert classify_page(_page(headings=["Checkout"], price_count=9)) is PageType.CHECKOUT  # wins over listing


def test_priority_login_beats_product_signals():
    form = FormInfo(action=None, method="post", submit_text="Sign in", fields=[_field("email"), _field("password")])
    page = _page(forms=[form], buttons=["Buy now"], price_count=5)
    assert classify_page(page) is PageType.LOGIN


def test_virtual_form_from_orphan_fields_is_login():
    virtual = FormInfo(action=None, method="js", submit_text="Submit", virtual=True,
                       fields=[_field(name="username"), _field("password", name="password")])
    assert classify_page(_page(forms=[virtual])) is PageType.LOGIN


def test_plain_page_is_content():
    assert classify_page(_page(url="https://a.example/about-us", headings=["About us"])) is PageType.CONTENT


@pytest.mark.parametrize("value", [t.value for t in PageType])
def test_page_type_values_fit_the_column(value):
    assert len(value) <= 100  # pages.page_type is String(100)
