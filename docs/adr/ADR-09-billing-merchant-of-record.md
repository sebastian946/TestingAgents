# ADR-09: Billing through a Merchant of Record, plans as monthly job allowances

- **Status:** Accepted
- **Date:** 2026-10-19
- **Ticket:** WTA-30

## Context

The product has a clear unit of cost: a job (about US$0.33 of LLM spend for 8 pages,
ADR-06). To charge for it we need plans, a way to take money from customers anywhere in the
world, and to know at `POST /jobs` time whether the caller may still create one.

The company is in Colombia and sells globally. That shapes the payment choice:

- **Stripe** does not open accounts in Colombia, and even where it does, the seller is
  responsible for collecting and remitting VAT/sales tax in every customer's jurisdiction
  (EU, UK, US states...). That is a tax-compliance project, not a payment integration.
- A **Merchant of Record** (Lemon Squeezy, Paddle) is the legal seller to the customer: it
  charges the card, issues the invoice, collects and remits the taxes, handles refunds and
  chargebacks, and pays us out. Higher fee (about 5% + US$0.50 against 2.9% + US$0.30),
  no tax work, available from Colombia.
- Local processors (Wompi, PayU, Mercado Pago) only make sense for a local market.

## Decision

**Lemon Squeezy as Merchant of Record. Plans are monthly job allowances. Our side is a
`subscriptions` row per user, written only by signed webhooks.**

- **Plans** (`config/plans.py`): Free 3 jobs/month, Starter 30 for US$29, Pro 150 for
  US$99. Prices live at the provider; the code only needs the allowance. Accounts without
  a subscription are on `DEFAULT_PLAN` (free in production).
- **Checkout is hosted** by the provider. The dashboard links to it with the user's email
  prefilled and our user id as custom data, so the webhook can tell us who bought. We
  never see a card number, so no PCI scope.
- **Webhooks are the only writer** of `subscriptions`. Every event is verified with an
  HMAC-SHA256 of the raw body before parsing; unknown events and unconfigured variants
  answer 200 and are ignored (so the provider stops retrying); an unknown user or a bad
  signature is a 400. The row keeps the provider's status, the plan, the period end and
  the customer-portal URL.
- **Entitlement** (`services/billing.plan_for`): `active`, `on_trial` and `past_due` keep
  the plan (past due = the provider is retrying the card; locking the user out on day one
  loses more customers than it saves); `cancelled` keeps it until the period end; anything
  else falls back to the default plan.
- **Enforcement** at `POST /jobs`: jobs created this calendar month (UTC) against the
  allowance, `402 Payment Required` with the numbers so the dashboard can offer the
  upgrade. The hourly cap (ADR-07) stays as abuse protection.
- **Managing the subscription** (invoices, card, cancellation) happens at the provider's
  customer portal, linked from the dashboard. Deleting an account removes our row but does
  not cancel the subscription: the product tells the user to cancel first.

## Consequences

- Selling to any country is possible from day one, with correct invoices and taxes, at the
  cost of a higher fee. Moving to Stripe later means replacing `services/billing.py`'s
  webhook parsing and the checkout links; the plans, the enforcement and the `subscriptions`
  table stay.
- Allowances reset on calendar months, not on the subscription's billing day. Simple to
  reason about and to test; the small unfairness (a mid-month upgrade gets the full
  allowance) favours the customer.
- A missed webhook (provider outage, our API down) leaves a user on the wrong plan until the
  next event. The provider retries failed deliveries, and the customer portal shows the
  truth; a nightly reconciliation against the provider's API is the next step if it ever
  matters.
- Usage-based billing (per job beyond the allowance) is not supported; the 402 is a hard
  stop. Adding metered overages is a provider feature plus a change in `usage_for`.
