# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Migration in progress. `prompts/` holds the migrated agent prompts (adapted milestone by milestone), `legacy/` holds verbatim upstream skills as a porting reference (see `PROVENANCE.md`), and `src/mozg_outreach/` holds the Python service.

- Plan: `docs/plans/2026-11-14-plugin-migration.md`. Follow its milestones and assumptions.
- Research behind it: `docs/research/2026-11-14-initial-research.md`.

## Commands

```bash
uv sync                                   # create .venv with dev deps (Python 3.12)
uv run pytest                             # offline tests (live tests are deselected by default)
uv run pytest tests/test_prompts.py::test_prompt_frontmatter_names_match_files   # one test
uv run pytest -m live                     # real Claude Code + web calls; costs subscription usage
pre-commit run --files <changed files>    # ruff, ruff-format, mdformat, json format, mypy --strict
uv run mozg-outreach                      # API on 127.0.0.1:8765; data in ~/.local/share/mozg-outreach (MOZG_OUTREACH_HOME)
```

`legacy/` is excluded from every hook and from ruff and mypy. Don't edit it; port from it.

## What this project is

A local web service with a UI for business and job/freelance outreach. Scope for now:

- **Local only.** The backend is Python. AI steps run on the locally installed Claude Code through the Agent SDK, using the user's subscription login, not the Anthropic API directly.
- **Project = brief.** The user writes a brief, the AI proposes the report structure and fields, and the user edits and accepts them.
- **Data sources per project**, chosen by the user: web search, Telegram, and job/company feeds.
- **Run.** Scans the chosen sources, finds matching items, scores them and loads a report into the UI.
- **Enrichment.** The user picks promising items and enriches them.
- **Registry.** People and companies live in one global registry shared by all projects. A lead links to many people and companies, each profile lists every source it was seen in, and the UI shows the connections as profiles and a graph. Exact identifiers merge automatically, and fuzzy matches are only suggested.
- **Generated text** (letters, messages, proposals) is always naturalized toward the user's own voice, which is built from their writing samples and style prompts.
- **No sending.** Drafts only.

## Origin of the pipelines

The pipelines come from the `mz-biz-outreach` and `mz-job-outreach` plugins in https://github.com/DoctorMozg/claude-pipelines (commit `695e580`, v0.57.0). The `expert-naturalizer` and `expert-copywriter` agents are forked from `mz-creative`. After migration this repo is the only home for the outreach pipelines and they are removed from `claude-pipelines`. The forked creative agents diverge from `mz-creative` on purpose. Don't sync them back.

## Architecture decisions

- **Python orchestrates; prompts don't.** Phases, dedup, filtering, scoring, state and resume live in Python. Each agent step is one Agent SDK call:

  - the agent prompt file is the system prompt and the step input is the user prompt;
  - a pydantic-derived JSON schema is the `output_format`, and the result is read from `ResultMessage.structured_output`.

  Agent prompts must not write files, emit `STATUS:` lines or reference `.mz/` paths.

- **Gates are UI steps between agent calls,** never `AskUserQuestion` inside a session. That also sidesteps anthropics/claude-code#76204, where `can_use_tool` dies after a subagent runs.

- **Fan-out is `asyncio` with a concurrency limit,** not agents dispatching subagents.

- **Scores are computed in Python** from per-axis features the agents return, with weights stored per project and a reason per axis. Never let a model compute a composite score.

- **Every extracted field is an envelope** `{value, source_url, quote, confidence, fetched_at}`. Contact data (emails, names) must come from a fetched page or a provider response, never from the model; a guessed email is labelled `guessed`.

- **Isolate SDK runs per project:** `cwd` is a per-project workspace, with `setting_sources=[]` and auto memory disabled, so user rules and this repo's `.mz/` (Claude Code memory) stay out of agent runs.

- **Naturalization is validated in Python.** The naturalizer's own metrics are model estimates. After each rewrite, check word budget, preserved entities, salutation and sign-off, and banned punctuation in Python, and revert to the snapshot on damage. Drafts use Medium severity (structure and wording) with ±10% length and the channel word budget, never Heavy or the generic "cut ≥15%" default.

- **Letter format:** a subject (email only), a salutation, a gratitude sign-off with the sender's first name, and plain ASCII punctuation (no em or en dashes, curly quotes or ellipsis characters). Python writes the salutation and sign-off from the project's `sender_name` and `sender_tone` (`letters/frame.py`); the copywriter returns only subject and body.

- **Prompt conventions** (`prompts/<group>/<name>.md`; see the adapted `prompts/biz/outreach-scout.md`):

  - Frontmatter keeps `name` (must equal the file stem), `model`, `effort` and `maxTurns`. `tools` lists only WebSearch/WebFetch, and `PromptLibrary` drops anything else.
  - `## Input` names the JSON fields of the pydantic input model.
  - `## Output Format` shows the exact output model shape, root always an object, with `concerns: [...]` replacing `STATUS`, `CONFLICT DETECTED` and `UNVERIFIED`.
  - Contracts live next to the pipeline, for example `src/mozg_outreach/pipelines/biz/models.py`. Offline pipeline tests replay recorded agent outputs with `RecordedRunner` (`tests/pipelines/conftest.py`).

- **Source adapters** emit a normalized `SourceItem` (research doc, section 5). A Telegram session file grants full account access: keep it outside workspaces and never put it in a prompt.
