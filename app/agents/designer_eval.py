"""Evaluate a Designer prompt version against the WTA-15 acceptance criteria.

Usage, from app/ (calls the real API, so it costs money; asks before running):
    uv run python -m agents.designer_eval --version v2
    uv run python -m agents.designer_eval --version v2 --model claude-sonnet-5-5
    uv run python -m agents.designer_eval --version v2 --model claude-haiku-4-5
    uv run python -m agents.designer_eval --compare        # table of saved runs, no API calls

Runs the Designer on fixed page inventories (a real login page and a real form captured by
the explorer, plus a checkout page with and without an owner description), checks each
criterion, prints the scenarios for human review and saves everything to
reports/designer_eval/<version>-<model>-<timestamp>.json so prompt versions and models
can be compared (ADR-02 records the model decision).

The checks are keyword heuristics in English and Spanish: they catch regressions, but a
human still reads the scenarios before a prompt version becomes the default.
"""
import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.designer import DesignResult, design_scenarios, model_profile
from config.variables import settings
from models.agent_models import TestScenario

PRIORITY_SCORE = {"low": 1, "medium": 2, "high": 3, "critical": 4}
CHECKOUT_CRITICAL = "Tienda online de ropa. Lo crítico es el checkout: es donde perdemos dinero si algo falla."


@dataclass
class EvalPage:
    url: str
    title: str | None
    page_type: str | None
    elements: dict[str, Any] | None


# Inventories captured by PlaywrightFetcher (WTA-11) from real pages, trimmed to the fields
# the Designer receives. Kept inline so the eval does not depend on the network.
LOGIN = EvalPage(
    url="https://www.saucedemo.com/",
    title="Swag Labs",
    page_type="login",
    elements={"forms": [{"method": "get", "submit_text": "Login", "fields": [
        {"tag": "input", "type": "text", "name": "user-name", "id": "user-name", "label": "Username", "placeholder": "Username"},
        {"tag": "input", "type": "password", "name": "password", "id": "password", "label": "Password", "placeholder": "Password"},
    ]}]},
)
CONTACT_FORM = EvalPage(
    url="https://demoqa.com/text-box",
    title="demosite",
    page_type="form",
    elements={"headings": ["Text Box"], "forms": [{"method": "get", "submit_text": "Submit", "fields": [
        {"tag": "input", "type": "text", "id": "userName", "label": "Full Name", "placeholder": "Full Name"},
        {"tag": "input", "type": "email", "id": "userEmail", "label": "Email", "placeholder": "name@example.com"},
        {"tag": "textarea", "type": "textarea", "id": "currentAddress", "label": "Current Address", "placeholder": "Current Address"},
        {"tag": "textarea", "type": "textarea", "id": "permanentAddress", "label": "Permanent Address"},
    ]}]},
)
CHECKOUT = EvalPage(
    url="https://shop.example/checkout",
    title="Checkout",
    page_type="checkout",
    elements={"headings": ["Checkout", "Order summary"], "forms": [{"method": "post", "submit_text": "Place order", "fields": [
        {"tag": "input", "type": "text", "name": "full_name", "label": "Full name", "required": True},
        {"tag": "input", "type": "email", "name": "email", "label": "Email", "required": True},
        {"tag": "input", "type": "text", "name": "address", "label": "Shipping address", "required": True},
        {"tag": "input", "type": "text", "name": "card_number", "label": "Card number", "required": True},
        {"tag": "input", "type": "text", "name": "card_expiry", "label": "Expiry (MM/YY)", "required": True},
        {"tag": "input", "type": "text", "name": "card_cvv", "label": "CVV", "required": True},
    ]}], "buttons": ["Apply coupon"]},
)

SUCCESS = re.compile(r"valid credentials|success|logs? in with valid|exitos|correctamente|credenciales v[aá]lidas", re.I)
INVALID = re.compile(r"invalid|wrong|incorrect|unknown|unregistered|inv[aá]lid|incorrect|err[oó]ne|desconocid|no registrad", re.I)
EMPTY = re.compile(r"empty|blank|without|missing|vac[ií]|sin (usuario|contrase|datos)|en blanco", re.I)


def _text(s: TestScenario) -> str:
    return f"{s.title} {' '.join(s.steps)} {s.expected_result}"


def _mean_priority(scenarios: list[TestScenario]) -> float:
    return sum(PRIORITY_SCORE[s.priority] for s in scenarios) / max(len(scenarios), 1)


def check_login(scenarios: list[TestScenario]) -> dict[str, bool]:
    invalid = [s for s in scenarios if INVALID.search(s.title)]
    return {
        "login: has a successful-login scenario": any(SUCCESS.search(s.title) for s in scenarios),
        "login: has an invalid-credentials scenario": bool(invalid),
        "login: has an empty-fields scenario": any(EMPTY.search(_text(s)) for s in scenarios),
        "login: invalid credentials are high/critical": bool(invalid) and all(s.priority in {"high", "critical"} for s in invalid),
    }


def check_description_effect(plain: list[TestScenario], weighted: list[TestScenario]) -> dict[str, bool]:
    deeper = len(weighted) > len(plain)
    higher = _mean_priority(weighted) > _mean_priority(plain)
    return {"description: 'checkout is critical' raises priority or depth": deeper or higher}


def _cost_estimate(n_calls: int, model: str) -> str:
    # Rough upper bound: ~3k input + ~4k output (scenarios plus thinking) per call
    p = model_profile(model)
    if not p.input_usd_per_mtok:
        return "unknown (model not in agents.designer.MODEL_PROFILES)"
    usd = n_calls * (3_000 * p.input_usd_per_mtok + 4_000 * p.output_usd_per_mtok) / 1_000_000
    return f"~US${usd:.2f}"


def compare(out_dir: Path) -> int:
    """Print the latest saved run per (prompt version, model, effort): no API calls."""
    latest: dict[tuple[str, str, str], dict] = {}
    for path in sorted(out_dir.glob("*.json")):  # names sort by timestamp
        run = json.loads(path.read_text(encoding="utf-8"))
        latest[(run["version"], run["model"], run.get("effort") or "-")] = run
    if not latest:
        print(f"No saved runs in {out_dir}")
        return 1
    print(f"{'version':8} {'model':20} {'effort':7} {'checks':>7} {'scen.':>6} {'retries':>8} {'out tok':>8} {'cost':>9}")
    for (version, model, effort), run in sorted(latest.items()):
        results = run["results"].values()
        passed = sum(run["checks"].values())
        print(f"{version:8} {model:20} {effort:7} {passed:>3}/{len(run['checks']):<3} "
              f"{sum(len(r['scenarios']) for r in results):>6} "
              f"{sum(r.get('attempts', 1) - 1 for r in results):>8} "
              f"{sum(r['output_tokens'] for r in results):>8} "
              f"{'US$' + format(sum(r.get('estimated_cost_usd', 0) for r in results), '.3f'):>9}")
    print("\nRead the scenarios themselves in the JSON files before deciding: the checks are heuristics.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=settings.designer_prompt_version)
    parser.add_argument("--model", default=settings.designer_model)
    parser.add_argument("--effort", default=settings.designer_effort, help="ignored by models without effort (Haiku 4.5)")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    parser.add_argument("--compare", action="store_true", help="compare saved runs instead of calling the API")
    args = parser.parse_args()
    out_dir = Path(settings.reports_dir) / "designer_eval"
    if args.compare:
        return compare(out_dir)
    effort = args.effort if model_profile(args.model).supports_effort else None

    runs = [
        ("login", LOGIN, None),
        ("contact_form", CONTACT_FORM, None),
        ("checkout_plain", CHECKOUT, None),
        ("checkout_critical", CHECKOUT, CHECKOUT_CRITICAL),
    ]
    print(f"Prompt {args.version} | model {args.model} | effort {effort or '(not supported)'}")
    print(f"{len(runs)} pages (up to {2 * len(runs)} calls with retries), estimated cost {_cost_estimate(len(runs), args.model)}.")
    if not args.yes and input("Run? [y/N] ").strip().lower() != "y":
        return 1

    results: dict[str, DesignResult] = {}
    for name, page, description in runs:
        print(f"\n=== {name} ({page.url}){' + description' if description else ''} ===", flush=True)
        result = design_scenarios(page, description, version=args.version, model=args.model, effort=args.effort)
        results[name] = result
        for s in result.scenarios:
            print(f"  [{s.priority:8}] [{s.category:13}] {s.title}")
        print(f"  tokens: in={result.input_tokens} out={result.output_tokens} "
              f"cache_read={result.cache_read_input_tokens} attempts={result.attempts} "
              f"cost=US${result.estimated_cost_usd:.4f} model={result.model}")

    checks = {
        "schema: every response parsed": True,  # design_scenarios raises otherwise
        **check_login(results["login"].scenarios),
        **check_description_effect(results["checkout_plain"].scenarios, results["checkout_critical"].scenarios),
    }
    plain, weighted = results["checkout_plain"].scenarios, results["checkout_critical"].scenarios
    print(f"\ncheckout without description: {len(plain)} scenarios, mean priority {_mean_priority(plain):.2f}")
    print(f"checkout 'is critical':       {len(weighted)} scenarios, mean priority {_mean_priority(weighted):.2f}")
    print("\nChecks:")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"{args.version}-{args.model}-{stamp}.json"
    out.write_text(json.dumps({
        "version": args.version,
        "model": args.model,
        "effort": effort,
        "checks": checks,
        "results": {
            name: {**asdict(r), "estimated_cost_usd": r.estimated_cost_usd,
                   "scenarios": [s.model_dump() for s in r.scenarios]}
            for name, r in results.items()
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
