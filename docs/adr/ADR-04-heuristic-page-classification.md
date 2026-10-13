# ADR-04: Page types classified by heuristics, not by an LLM

- **Status:** Accepted
- **Date:** 2026-10-06
- **Ticket:** WTA-13

## Context

A login page is tested differently from a product page or a blog post, so the Designer
needs each page's type: login, signup, checkout, product, listing, form or content. An LLM
could label pages well. But the label is one word, and the inventory already carries the
signals a person would use: a password field, prices, an "add to cart" button, many similar
links.

## Decision

**`classify_page` is a pure function with ordered rules, where the first match wins.** It
uses the inventory and a few counters that the browser computes in the same extraction
(prices, stock words, repeated buttons, links that share a path prefix, words per link).
The rules and thresholds are documented in the module and in the guide.

The thresholds come from real pages, not from intuition. Two examples:
- Wikipedia was first classified as a listing, because of its 500 similar links. A
  words-per-link threshold fixed it: an article has many words around its links, a listing
  has few.
- A catalog was first classified as a product, because of its "Add to basket" buttons.
  Counting repeated buttons fixed it: a grid of cards repeats the same button many times.

## Consequences

- Classifying costs nothing, takes microseconds and gives the same answer every time. It is
  tested with plain assertions, and every misclassified real page becomes a unit test before
  the rules are tuned.
- 9 of 9 real pages were classified correctly when this was measured. Unusual layouts
  can still fool the rules; when the heuristic type looks wrong, the Designer prompt tells
  the model to trust the inventory over it.
- This is a deliberate example of not using an LLM where plain code is cheaper, faster and
  good enough.
