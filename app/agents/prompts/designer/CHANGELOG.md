# Designer prompt versions

Prompts are versioned artifacts: every change gets a new folder (`vN/system.md` +
`vN/user.md`), old versions are never edited, and `DESIGNER_PROMPT_VERSION` picks the one
in use. Compare versions with the eval before switching the default:

```powershell
uv run python -m agents.designer_eval --version v1
uv run python -m agents.designer_eval --version v2
```

Each run is saved under `reports/designer_eval/` so results can be diffed later.

## v1 — baseline (first draft)

The original draft, kept as written so later versions have something to be measured against.
Known issues it was replaced for:

- Page data and the app description were formatted into the system prompt, so the system
  prompt changed on every page (no prompt caching possible) and the description had no
  stated role.
- No priority rubric: priorities were "high/medium/low" with no meaning attached, and no
  `critical` level for blocked core flows or security risks.
- Categories did not fit manual functional testing (`performance` cannot be checked from a
  single page inventory) and lacked `negative` and `validation`, the bulk of form testing.
- No coverage guidance per page type, so a login page might never get an "empty fields" case.
- Offered two output styles (Cucumber or step by step), which a fixed schema cannot honor.
- No guard against instructions embedded in third-party page content.

## v2 — current default

- Static system prompt (cacheable) with all instructions; the page and the description go in
  the user message inside XML tags (`<application_description>`, `<page>`, `<page_inventory>`).
- Explains *why* scenarios must be specific and grounded in the inventory, and what makes a
  step executable (named element, concrete data, `<placeholder>` for unknown accounts).
- Priority rubric tied to business impact, with an explicit rule that rejecting invalid
  credentials is never `low`/`medium` (acceptance criterion of WTA-15).
- Category set matched to manual web testing: functional, negative, validation, security,
  usability, accessibility.
- Coverage checklist per page type, with "trust the inventory if the heuristic type is wrong".
- The app description has two explicit uses: domain context, and raising priority plus depth
  for the areas the owner calls critical (acceptance criterion of WTA-15).
- Third-party page content is declared data, not instructions (prompt injection guard).
- One worked example on a neutral form (newsletter) to show the level of detail without
  biasing every page toward login.
- Output format is enforced by the API (structured outputs) rather than described in prose.

## v3 — next iteration

To be written from the results of running the eval on v2 with real pages: look at the
scenarios it produced, note what a senior QA would change (missing cases, vague steps,
miscalibrated priorities), change one thing at a time, and record the before/after here.
