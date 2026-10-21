"""Subscription plans (WTA-30, ADR-09).

A plan is a monthly job allowance. The job is the unit of cost (about US$0.33 of LLM spend
for 8 pages, ADR-06), so the allowance is what the price buys. Prices are informational
here: the checkout and the charge happen at the payment provider (a Merchant of Record),
which tells us through a webhook which plan a user is on.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    jobs_per_month: int
    price_usd: int  # per month; 0 = free
    # WTA-33: automated test runs (the Executor) included per month; 0 = not available
    test_runs_per_month: int = 0
    # Free trial days offered at the provider (the variant's "free trial" setting must
    # match): informational, shown on the landing and the plan page. 0 = no trial
    trial_days: int = 0


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Free", jobs_per_month=3, price_usd=0),
    "starter": Plan("starter", "Starter", jobs_per_month=10, price_usd=19),
    "pro": Plan("pro", "Pro", jobs_per_month=40, price_usd=49, test_runs_per_month=10, trial_days=7),
}

FREE = PLANS["free"]
