# ADR-02: Model for the Designer agent

- **Status:** Proposed. The quality comparison on real pages is still pending (see "How to
  close this ADR").
- **Date:** 2026-10-08
- **Ticket:** WTA-16
- **Related:** ADR-01 (the exploration is deterministic, with no LLM), WTA-15 (Designer prompt)

## Context

The Designer is the only LLM call in the MVP: one request per crawled page, up to 8 pages
per job, and two requests when a response fails validation. It turns the page inventory and
the app description into the test scenarios the customer pays for. A scenario that is
vague, misses the obvious negative cases or has the wrong priority makes the product
worthless. So quality comes first, and cost is a constraint that has to stay predictable.

The board asked to choose between Haiku and Sonnet. These are the current candidates
(Claude API list prices, 2026-09):

| Model | Input / output per MTok | Effort control | Refusal fallback | Notes |
|---|---|---|---|---|
| `claude-opus-5-5` | $4 / $20 | yes (`low` to `max`) | yes | Most capable Opus; reasoning is always on |
| `claude-sonnet-5-5` | $2 / $10 | yes | yes | Half the price of Opus 5.5 |
| `claude-haiku-4-5` | $1 / $5 | no (the API rejects `effort`) | no | Cheapest; 200K context, which is plenty here |

All three support structured outputs, which the Designer relies on. `agents/designer.py`
(`MODEL_PROFILES`) sends each model only the fields it accepts. Switching model is therefore
a change to `DESIGNER_MODEL` in `.env`, with no code change.

### Cost estimate per job

The input per page is about 1.5K tokens of system prompt plus about 0.3K of page data. The
output is about 1.5K tokens of scenarios, plus reasoning tokens on the models that reason
(about 2.5K more at effort `high`; Haiku 4.5 does not reason unless asked to). For 8 pages:

| Model | Input | Output | Per job |
|---|---|---|---|
| Opus 5.5 (effort `high`) | 8 × 1.8K × $4/M ≈ $0.06 | 8 × 4K × $20/M ≈ $0.64 | **≈ $0.70** |
| Sonnet 5.5 (effort `high`) | 8 × 1.8K × $2/M ≈ $0.03 | 8 × 4K × $10/M ≈ $0.32 | **≈ $0.35** |
| Haiku 4.5 | 8 × 1.8K × $1/M ≈ $0.01 | 8 × 1.5K × $5/M ≈ $0.06 | **≈ $0.08** |

These are estimates. Output dominates the cost, and the reasoning tokens are the most
uncertain part. A self-healing retry roughly doubles the cost of that page.

**Measured (WTA-18, 2026-10-09):** two real jobs on a login page with Opus 5.5 at effort
`high` cost $0.058 and $0.033 per page, with about 1.5K to 2.2K output tokens. The system
prompt (about 2.7K tokens) is cached: the second call read it at 0.1x. A warm-cache page
costs about $0.04, so an 8-page job is about $0.33, half of the estimate above. Every
job's real cost is in `llm_calls` (README, "LLM tokens and cost").

## Decision (provisional)

**Keep `claude-opus-5-5` at effort `high` as the default until the comparison below is run.**

- The cost of a bad scenario is a customer who stops paying, while the cost gap is cents per
  job: even at Opus prices a job costs less than $1.
- `DESIGNER_MODEL` and `DESIGNER_EFFORT` make the switch a configuration change, so there is
  nothing to lose by deciding with measurements instead of guesses.

Criteria for switching after the comparison:

1. **Sonnet 5.5** becomes the default if it passes every eval check, and a side-by-side
   human review of the scenarios on the same pages finds no meaningful loss: no missing
   negative or validation cases, no miscalibrated priorities, steps just as executable. That
   halves the cost.
2. **Haiku 4.5** becomes the default only if it also meets bar 1. Its lack of reasoning and
   of effort control makes that unlikely for the depth WTA-15 asks for (the description has
   to change priorities and depth). It is the better fit for a future "quick scan" plan.
3. If neither passes, try **Opus 5.5 at effort `medium`** before giving up on lowering cost:
   on recent models, a lower effort often holds quality better than a smaller model.

## How to close this ADR

The comparison uses the 4 eval pages: a real login, a real form, and a checkout with and
without "checkout is critical". It costs about $0.70 for Opus, $0.35 for Sonnet and $0.10
for Haiku, and needs a real `ANTHROPIC_API_KEY`:

```powershell
cd app
uv run python -m agents.designer_eval --model claude-opus-5-5
uv run python -m agents.designer_eval --model claude-sonnet-5-5
uv run python -m agents.designer_eval --model claude-haiku-4-5
uv run python -m agents.designer_eval --compare      # checks, scenarios, retries, tokens, cost
```

Then read the scenarios in `reports/designer_eval/*.json`; the checks are keyword heuristics
and do not judge quality. Record the result here: a table with checks passed, cost per page
and the review notes, then the final decision. Set **Status** to *Accepted*.

## Consequences

- Model choice is configuration, not code: a new model needs an entry in `MODEL_PROFILES`
  (accepted fields and prices). An unknown id still works, but without the optional fields
  and with no cost estimate.
- Prices live in `MODEL_PROFILES` and must be updated when Anthropic changes them. The cost
  logging of WTA-18 will read them from there.
- If a cheaper model is chosen later, the prompt may need its own version (`v3`), because
  prompts are tuned per model.
