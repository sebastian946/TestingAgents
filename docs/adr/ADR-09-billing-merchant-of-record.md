# ADR-09: Billing through Stripe, plans as monthly job allowances

- **Status:** Accepted (revised 2026-10-20: Stripe replaces Lemon Squeezy, WTA-34)
- **Date:** 2026-10-19
- **Ticket:** WTA-30, WTA-34

## Context

The product has a clear unit of cost: a job (about US$0.33 of LLM spend for 8 pages,
ADR-06). To charge for it we need plans, a way to take money from customers anywhere in the
world, and to know at `POST /jobs` time whether the caller may still create one.

The company is in Colombia and sells globally. The first version of this decision (WTA-30)
chose a **Merchant of Record** (Lemon Squeezy) because it is the legal seller, handles taxes
and invoices, and is open to Colombian companies. The owner then opened a **Stripe**
account, which changes the trade-off:

- **Stripe** charges about 2.9% + US$0.30 per card payment (plus 1% for international
  cards and currency conversion). It has hosted Checkout and a Customer Portal, so we still
  never see a card number, and a mature SDK with signed webhooks. Taxes are our problem:
  Stripe Tax can calculate and collect them (`STRIPE_AUTOMATIC_TAX=true`), but registering
  and remitting in each jurisdiction is on us once we cross the local thresholds.
- A **Merchant of Record** (Lemon Squeezy, Paddle) removes the tax work at about 5% +
  US$0.50 per sale and a slower payout.
- Local processors (Wompi, PayU, Mercado Pago) only make sense for a local market.

## Decision

**Stripe Checkout + Customer Portal + webhooks. Plans are monthly job allowances. Our
side is a `subscriptions` row per user, written only by signed webhooks.**

- **Plans** (`config/plans.py`): Free 3 jobs/month, Starter 10 for US$19, Pro 40 for
  US$49 and 10 automated test runs (ADR-10). Prices live in Stripe as two recurring Prices;
  `STRIPE_PRICE_STARTER` / `STRIPE_PRICE_PRO` map them to our plan codes. Accounts without a
  subscription are on `DEFAULT_PLAN` (free in production).
- **Checkout is hosted** by Stripe: `POST /billing/checkout {plan}` creates a Checkout
  Session (mode `subscription`, the user's email or existing Stripe customer, our user id
  as `client_reference_id` and in the subscription metadata, promotion codes allowed,
  optional automatic tax) and the dashboard redirects to its URL. Success and cancel return
  to `/billing?checkout=...`.
- **Webhooks are the only writer** of `subscriptions`. `POST /billing/webhook/stripe`
  verifies `Stripe-Signature` with the SDK before parsing. `checkout.session.completed`
  retrieves the subscription; `customer.subscription.created/updated/deleted` upsert the
  row (status mapped to ours, `cancel_at_period_end` -> `cancelled`, the period end and
  the Stripe customer id). Unknown events and unknown prices answer 200 and are ignored;
  an unknown user or a bad signature is a 400.
- **Entitlement** (`services/billing.plan_for`): `active`, `on_trial` and `past_due` keep
  the plan (past due = Stripe is retrying the card); `cancelled` keeps it until the period
  end; anything else falls back to the default plan.
- **Enforcement** at `POST /jobs`: jobs created this calendar month (UTC) against the
  allowance, `402 Payment Required` with the numbers so the dashboard can offer the
  upgrade. The hourly cap (ADR-07) stays as abuse protection.
- **Managing the subscription** (invoices, card, plan change, cancellation) happens in
  the Stripe Customer Portal: `POST /billing/portal` creates a portal session for the
  stored customer id. Deleting an account removes our row but does not cancel the
  subscription: the product tells the user to cancel first.

## Consequences

- Lower fees and one provider for everything, at the cost of owning tax compliance.
  Until revenue justifies registrations abroad, prices are "USD, taxes may apply" and
  Stripe Tax stays off; turning it on is one setting plus a Stripe Tax registration.
- Moving back to a Merchant of Record later means replacing `services/billing.py`'s
  checkout/portal/webhook code; the plans, the enforcement and the `subscriptions`
  table stay (the `customer_portal_url` column is unused with Stripe and kept for that).
- Allowances reset on calendar months, not on the subscription's billing day. Simple to
  reason about and to test; the small unfairness (a mid-month upgrade gets the full
  allowance) favours the customer.
- A missed webhook leaves a user on the wrong plan until the next event. Stripe retries
  failed deliveries for days and the portal shows the truth; a nightly reconciliation
  against the Stripe API is the next step if it ever matters.
- Usage-based billing (per job beyond the allowance) is not supported; the 402 is a hard
  stop. Metered prices in Stripe plus a change in `usage_for` would add overages.
