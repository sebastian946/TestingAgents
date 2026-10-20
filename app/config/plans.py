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


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Free", jobs_per_month=3, price_usd=0),
    "starter": Plan("starter", "Starter", jobs_per_month=10, price_usd=19),
    "pro": Plan("pro", "Pro", jobs_per_month=40, price_usd=49),
}

FREE = PLANS["free"]
