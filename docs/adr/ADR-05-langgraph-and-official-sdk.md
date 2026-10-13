# ADR-05: LangGraph for the pipeline, the Anthropic SDK with structured outputs for the LLM

- **Status:** Accepted
- **Date:** 2026-10-11
- **Tickets:** WTA-15/16 (Designer), WTA-21 (graph)

## Context

The pipeline has a fixed shape: explore, design, document, plus a way to stop early when
there is nothing to work with. Only one step calls an LLM: the Designer, once per page. Two
questions came up:

- **How to orchestrate the steps.** One option is a single long function; the other is an
  explicit graph.
- **How to call Claude and get data back that we can store.** The options were
  LangChain's `ChatAnthropic` and the official SDK, with free-form JSON or structured
  outputs.

## Decision

**A LangGraph `StateGraph` orchestrates plain Python nodes. The Designer calls Claude through
the official `anthropic` SDK, with structured outputs.**

- **The graph:**
  - Nodes: `explorer`, `designer`, `documenter`, `fail`.
  - Two conditional edges: no pages goes to `fail`, and no scenarios goes to `fail`. Having
    nothing to work with is a path of the graph, not an exception; exceptions are kept for
    real errors.
  - The state is a typed dict of plain data, and each node writes only the keys it owns.
- **The LLM call:**
  - The response is constrained to a JSON schema. Pydantic then checks the rules a schema
    cannot express: non-blank text, unique titles, at most 12 scenarios.
  - A response that fails validation gets exactly one self-healing retry, with the errors
    sent back as feedback.
  - The SDK already retries 429, 529 and 5xx errors with backoff, so we do not.
  - The model, the effort and the prompt version are settings. The prompts are versioned
    files (`v1`, `v2`, `v3`), and a released version is never edited.
- **No `ChatAnthropic`.** Every option of the call is a direct, documented parameter: effort,
  structured outputs, server-side fallbacks, prompt caching. LangSmith still traces each
  call, through `wrap_anthropic`.

## Consequences

- The pipeline can be read and tested node by node. LangSmith shows each run as a tree:
  the graph, its nodes and each Claude call with its tokens.
- Stored scenarios always have a valid shape, and broken output costs at most one extra call.
- The system prompt is static, while everything that changes per page goes in the user
  message. The prompt cache works: in real runs the second call read about 2.7K tokens at
  0.1x the input price.
- Two dependencies to keep in step: LangSmith 0.14.4 crashed with the 1.x SDK and needed
  0.14.7. Tests at the HTTP level, with the real SDK, catch this kind of breakage.
