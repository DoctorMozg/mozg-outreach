# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Migration done (M0 to M11). `prompts/` holds the adapted agent prompts (origin in `PROVENANCE.md`), `src/mozg_outreach/` the Python service and `ui/` the React UI.

- Plan: `docs/plans/2026-11-14-plugin-migration.md`. Follow its milestones and assumptions.
- Research behind it: `docs/research/2026-11-14-initial-research.md`.

## Commands

```bash
uv sync                                   # create .venv with dev deps (Python 3.12)
uv run pytest                             # offline tests (live tests are deselected by default)
uv run pytest tests/agents/test_prompt.py::TestParse::test_name_must_match_file   # one test
uv run pytest -m live                     # real Claude Code + web calls; costs subscription usage
pre-commit run --files <changed files>    # ruff, ruff-format, mdformat, json format, mypy --strict, tsc
uv run mozg-outreach                      # API on 127.0.0.1:8742; data in ~/.local/share/mozg-outreach (MOZG_OUTREACH_HOME)
                                          # loads ./.env first; variables set in the shell win
uv run python -m mozg_outreach.storage.revision "add x"   # autogenerate a migration after a model change; startup applies it
cd ui && npm ci --ignore-scripts && npm run build   # build the UI into ui/dist, which the API serves at /
cd ui && npm run dev                      # Vite on :5173, proxying /api to the running service
```

## What this project is

A local web service with a UI for business and job/freelance outreach. Scope for now:

- **Local only.** The backend is Python. AI steps run on the locally installed Claude Code through the Agent SDK, using the user's subscription login, not the Anthropic API directly.
- **Project = brief.** The user writes a brief, the AI proposes the report structure and fields, and the user edits and accepts them.
- **Data sources per project**, chosen by the user: web search, Telegram, and job/company feeds.
- **Run.** Scans the chosen sources, finds matching items, scores them and loads a report into the UI.
- **Enrichment.** The user picks promising items and enriches them.
- **Registry.** People and companies live in one global registry shared by all projects. A lead links to many people and companies, each profile lists every source it was seen in, and the UI shows the connections as profiles and a graph. Exact identifiers merge automatically, and fuzzy matches are only suggested.
- **Generated text** (letters, messages, proposals) is always naturalized toward the user's own voice, which is built from their writing samples and style prompts.
- **No sending.** Drafts only. Email drafts can go into the user's Gmail Drafts over IMAP, and the user sends them from Gmail; the app has no SMTP code.

## Origin of the pipelines

The pipelines come from the `mz-biz-outreach` and `mz-job-outreach` plugins in https://github.com/DoctorMozg/claude-pipelines (commit `695e580`, v0.57.0). The `expert-naturalizer` and `expert-copywriter` agents are forked from `mz-creative`. After migration this repo is the only home for the outreach pipelines and they are removed from `claude-pipelines`. The forked creative agents diverge from `mz-creative` on purpose. Don't sync them back.

## Layout

- `runs/`: flows as phase lists (`FlowSpec`), `RunManager` (one task per run, state saved after every phase, gates, resume), and the run event log behind SSE.
- `api/`: FastAPI app and routes; `ui/` mirrors its response types in `ui/src/api.ts`, so change both together.
- `browser/`: guarded headless Chrome and the agents' read-only `browser` toolkit. `apply/`: application form filling (field scan, protected-question policy, ATS profiles, safe actor) and the visible Chrome where the user submits (`seeker.apply` in `runs/apply.py`).
- `mail/`: Gmail over IMAP with a Workspace app password from `MOZG_OUTREACH_GMAIL_ADDRESS` and `MOZG_OUTREACH_GMAIL_APP_PASSWORD`. It pushes email drafts into Gmail Drafts and polls their threads to log sent, replied and bounced.
- `pipelines/{biz,seeker}/`: agent steps and their pydantic contracts; `scoring/`, `letters/`, `naturalize/`, `registry/`, `sources/` and `storage/` hold the shared parts.

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

- **Letter format:** a subject (email only), a salutation, a gratitude sign-off with the sender's first name, and plain ASCII punctuation (no em or en dashes, curly quotes or ellipsis characters). Python writes the salutation and sign-off from the project's `sender_name` and `sender_tone`, in the letter's language (`letters/frame.py`); a job or gig letter is in its posting's language (`letters/language.py`). The copywriter returns only subject and body. Letters to GCC companies get an opt-out line above the sign-off, added after naturalizing (`LetterFrame.finish`).

- **Prompt conventions** (`prompts/<group>/<name>.md`; see the adapted `prompts/biz/outreach-scout.md`):

  - Frontmatter keeps `name` (must equal the file stem), `model`, `effort` and `maxTurns`. `tools` lists only WebSearch/WebFetch, and `PromptLibrary` drops anything else. `toolkits: browser` adds the read-only browser tools (`browser/toolkit.py`), mounted per run by `AgentRunner`.
  - `## Input` names the JSON fields of the pydantic input model.
  - `## Output Format` shows the exact output model shape, root always an object, with `concerns: [...]` replacing `STATUS`, `CONFLICT DETECTED` and `UNVERIFIED`.
  - Contracts live next to the pipeline, for example `src/mozg_outreach/pipelines/biz/models.py`. Offline pipeline tests replay recorded agent outputs with `RecordedRunner` (`tests/pipelines/conftest.py`).

- **Source adapters** emit a normalized `SourceItem` (research doc, section 5). A Telegram session file grants full account access: keep it outside workspaces and never put it in a prompt.
