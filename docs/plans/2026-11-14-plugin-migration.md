# Plan: migrate outreach plugins into the service

Decisions (2026-11-14):

- `mz-biz-outreach` and `mz-job-outreach` move here and are removed from `claude-pipelines` afterwards; this repo becomes their only home.
- **Python orchestrates.** Skill markdown (phases, gates, dedup, scoring, state) becomes Python. Each agent step is one Agent SDK call: the migrated agent prompt as system prompt, the step input as user prompt, a pydantic-derived JSON schema as `output_format`, results read from `ResultMessage.structured_output`. Approval gates are UI steps between calls, so no `AskUserQuestion` runs inside a session and anthropics/claude-code#76204 no longer applies.
- `expert-naturalizer`, `expert-copywriter` and `forbidden-punctuation.sh` are forked from `mz-creative` and adapted here.
- Plain copy from `claude-pipelines` commit `695e580` (plugins v0.57.0) with a provenance note; no history import.
- **Registry of people and companies (2026-11-14).** One global registry across projects. A lead (biz item, job, gig) links to many people and companies, and every profile lists every source it was seen in. Exact identifiers (domain, email, LinkedIn URL, Telegram handle) merge automatically; fuzzy name matches become merge suggestions for the user. The UI shows connections, including a graph view.

Background: `docs/research/2026-11-14-initial-research.md`.

## Assumptions (override before approval)

- Python 3.12, `uv` for env and lockfile, `ruff` lint and format, `pyright` typecheck, `pytest` + `pytest-asyncio`, all run through `pre-commit`.
- Runtime dependencies, added when their milestone starts: `claude-agent-sdk` (M0), `pydantic` (M2), `sqlalchemy` 2.x on SQLite (M3), `fastapi` + `uvicorn` + `sse-starlette` (M9), `httpx` (M8), `telethon` (M8). No Alembic until the schema stabilizes; `create_all` for the prototype.
- UI stack (decided 2026-11-14): React + Vite + TypeScript SPA in `ui/`, built by Vite and served by FastAPI, with @tanstack/react-query and cytoscape for the graph. The API stays UI-agnostic.
- Prompts live as markdown files with frontmatter (`tools`, `model`) under `prompts/`, not inlined in Python, so they stay editable and diffable.
- Upstream prompt-lint tests and evals are not migrated. They are replaced with schema contract tests, recorded-response fixtures and a small opt-in live suite (`pytest -m live`).

## Layout

```
src/mozg_outreach/
  agents/        # AgentRunner: prompt loading, SDK options, structured output, cost, events
  pipelines/     # biz/, job/, freelance/: phase functions + pipeline state machine
  scoring/       # Python scorers from per-axis features
  naturalize/    # voice profiles, naturalizer dispatch, validation, revert
  sources/       # SourceAdapter protocol + adapters (telegram, ats, feeds)
  storage/       # SQLAlchemy models, repositories
  api/           # FastAPI routes, SSE, gate endpoints
prompts/
  biz/  job/  creative/   # migrated agent prompts (adapted)
legacy/                    # verbatim skill files, porting reference only; deleted in M11
tests/
```

## Milestones

Each milestone stays within about 10 files and 400 changed lines, except the mechanical copy in M1. Every milestone ends with `pre-commit run --all-files` and `pytest` green.

**M0 Spike: SDK as an agent-step runner** (scratch script, not committed)

- One `query()` with `system_prompt` from a file, `tools=["WebSearch","WebFetch"]`, `output_format` JSON schema, `cwd` in a temp workspace, `setting_sources=[]`, auto memory off.
- Three such calls concurrently; one interrupted mid-run.
- verify: validated `structured_output` printed, `total_cost_usd` and `model_usage` present, subscription auth used (no `ANTHROPIC_API_KEY`), interrupt leaves no orphan process. If `output_format` and tools don't combine, fall back to a final `submit_result` MCP tool and record that.
- **Result (2026-11-14, SDK 0.2.182, CLI 2.1.325): passed.**
  - `output_format` and tools work together; the CLI injects a `StructuredOutput` tool.
  - Three concurrent calls each returned schema-valid output in 17-27 s.
  - Auth was the subscription (`apiKeySource: none`).
  - `interrupt()` produced `subtype=error_during_execution, terminal_reason=aborted_tools`, and no orphan CLI processes were left.
  - Cost is about $0.35-0.39 per small scout call (sonnet, plus haiku in `model_usage`). Budget fan-out accordingly.
  - One scout returned a `pages.dev/llms.txt` "company", which confirms that source grounding must be verified in Python.

**M1 Scaffold and import** (mechanical)

- `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, `.gitignore` (`.mz/`, workspaces, `.env`, `*.session`), package skeleton.
- Copy agents to `prompts/{biz,job,creative}/`, skill files to `legacy/`, canonical source JSON to `prompts/job/sources/`; `PROVENANCE.md` with source repo, commit, version and MIT notice.
- verify: `pre-commit run --all-files` passes; file count matches upstream (26 agents + 2 creative agents + 2 source lists); `pytest` runs an empty suite.

**M2 Agent runner**

- `AgentRunner.run(agent, payload, result_type) -> AgentResult[T]` (value, cost, usage, session_id, duration). Reads prompt frontmatter for `tools` and `model`; builds options per run workspace; streams events to a callback; enforces `max_budget_usd`; maps SDK errors to typed exceptions.
- verify: unit tests with a fake SDK transport (schema mismatch raises, budget passed through, events emitted in order); `pytest -m live` runs `outreach-strategist` once and validates its result model.

**M3 Domain and storage**

- Models: Project (mode biz | job | freelance, sender name, voice settings), Brief, FieldSpec (versioned, stable field ids), SourceConfig, Run (phase, status, gate state), Item (with `{value, source_url, quote, confidence, fetched_at}` field envelopes), Score (per-axis + composite), Draft (versions, naturalization report), VoiceSample, RunEvent (seq).
- verify: repository tests on a temp SQLite file covering FieldSpec versioning and RunEvent ordering.

**M4 Biz pipeline, front half**

- Brief intake as a structured form plus an LLM gap check (replaces the AskUserQuestion loop); field proposal agent (new prompt) producing a FieldSpec draft; strategist; source researcher; scouts fanned out with `asyncio` and a concurrency limit; dedup by normalized domain in Python (drops `_scout_notes`).
- Adapt prompts: return structured output instead of writing files, drop STATUS lines, drop `.mz/` paths.
- verify: recorded-fixture test runs brief to deduped candidates offline; live smoke on a tiny brief (limit 3).
- **Result: done.**
  - The live chain (brief review, field proposal, strategy, sources, 2 scouts) ran on an Estonia B2B SaaS brief for $2.29 and produced the fixtures in `tests/pipelines/biz/fixtures/`.
  - `scout_results.json` was rebuilt from the recorded scout output, with one added duplicate and one `_scout_notes` row to exercise dedup.
  - Scout yield is low: 2 companies from 2 sources. Directories returned HTTP 403, and the strict grounding rule pushed most leads into `concerns`. Tune this in M5 by scouting more sources and turning "seen only in search snippets" into a lower-confidence field rather than a drop.
  - Deferred to M5: climate research (`outreach-climate-source-finder`, `outreach-climate-researcher`) as an optional step that feeds the strategist.

**M5 Biz pipeline, back half**

Split into two parts to stay within the milestone size cap:

- **M5a: enrichment.** Adapt the scanner, contact, news, growth and tech prompts. Add `enrichment_models.py` (provenance on every contact; email `kind: published | pattern_guess`), and `BizEnrichment`, which runs the five agents per company with failures turned into concerns.
- **M5b: report fields and scoring.**
  - A new `field-researcher` agent fills the accepted FieldSpec per company as FieldValue envelopes.
  - A new toolless `fit-judge` agent returns discrete features: sector match `exact | adjacent | unrelated`, which strategy must-have, strong-positive and disqualifying signals are present, and a quote for each.
  - `scoring/biz.py` computes six axes:
    - `data_completeness`: enrichment sections present plus required fields filled.
    - `review_reputation`: the legacy rating map.
    - `contact_availability`: the legacy formula, capped at 100.
    - `growth_signals`: the legacy map, with every trajectory value mapped.
    - `sector_relevance`: from the fit judge.
    - `outreach_feasibility`: must-have coverage plus strong positives, and 0 when a disqualifier is hit.
  - Weights are normalized by their sum, and each axis carries a reason.
  - `PipelineSteps` (`pipelines/steps.py`) runs named agent steps and totals cost; discovery, enrichment and assessment share it.
  - The scanner no longer reports `review_summary`; the reputation average is count-weighted in Python.
- **M5c: run-time assessment and report.**
  - A run scores each company without enrichment: field research, then the fit judge on scouted plus researched `facts` (about $0.55 per company). Enrichment runs only for items the user selects, then the fit judge reruns with the dossier and the item is rescored.
  - `ScoreAxis.value` is `None` for axes that aren't assessed yet; the total is the weighted mean of assessed axes.
  - `Item` stores `score` (full JSON), `score_total` (indexed, for ordering) and generic `details`; `ItemRepository.report` lists items best first.
  - Optional climate research (`BizDiscovery.research_climate`) feeds `StrategyInput.climate`.
  - The scout keeps snippet-only companies with `evidence: search_snippet` and falls back to `site:` searches on HTTP 403.
  - The field proposer, brief reviewer and strategist write prose in the brief's language.
  - Live check ($4.44): scout yield rose from 2 to 8 companies from 3 sources (4 `page`, 4 `search_snippet`). Climate research found 11 signals, all with a `source_url`. Run-time assessment costs about $0.50 per company.
  - The live check exposed `outreach_feasibility` saturating at 100, which ranked a company that fails a must-have first. Now feasibility is `70 * must-have coverage + 30 * strong-positive share`, capped at 40 when any must-have fails. The scout must leave `domain` null unless it saw it.

Original scope:

- Scanner, contact/news/growth/tech finders fanned out per company (the `enrichment-orchestrator` agent is removed); scoring in `scoring/biz.py` with per-axis reasons and weights from strategy; report assembled from DB, not by an LLM card writer unless a summary is wanted.
- verify: scorer unit tests (weights, missing data, unmapped growth categories); fixture test from candidates to scored items.

**M6 Naturalization**

- Port `forbidden-punctuation.sh` to Python and add the missing checks (curly quotes, `-`, `--`, `...`, banned characters inside quotes).
- Voice profile builder: measured features in Python (sentence length mean and variance, contraction rate, punctuation habits, greetings and sign-offs) plus an LLM qualitative pass; user style prompts appended verbatim; per register.
- Naturalize dispatch adapted for short texts (Light severity, ±5% length, word budgets, preserve list, register, retrieved genre-matched exemplars); validation and snapshot revert in Python; `[SPECIFICITY NEEDED]` moved into review notes.
- verify: validator tests on dirty and clean texts; revert tests (length drift, dropped entity, lost sign-off); stylometric-distance helper tested on fixtures.
- Split:
  - **M6a (done):** `naturalize/punctuation.py` (ASCII punctuation check, superset of the legacy script; quoted text is not exempt in drafts), `naturalize/validation.py` (length drift, word budget, dropped preserve terms, numbers, URLs and emails, salutation and sign-off lines kept verbatim, new punctuation; revert on damage; markers to review notes), `NaturalizeInput`/`NaturalizeResult`, and the adapted toolless `expert-naturalizer` prompt.
  - **M6b (done):** `naturalize/stylometry.py` (measured habits and a 0-1 distance), `naturalize/voice.py` with the toolless `voice-profiler` agent (at least 3 samples; user style prompts appended verbatim and ranked above everything else), and `VoiceRepository` with samples per channel and the latest profile per channel.
  - **M6c (done):** `Naturalizer` uses medium severity (the user chose structure fixes over minimal edits after the live check), ±10% length, and body-word budgets per channel from the legacy writers (email 120-180, DM 60-100, cover letter up to 199, proposal 280-350). Exemplars are the samples closest in length. Soft punctuation is allowed when it appears in at least half the samples. An agent failure keeps the draft. Budgets and drift count body words only.
  - Live check ($0.37): the profile was specific and correct; the light rewrite stayed within limits (128 to 124 body words), and three vague claims went to review notes instead of being invented.
  - Medium severity, chosen by the user: a rewrite that breaks a limit gets one retry with its previous attempt and the broken limits as `retry` feedback; only a second miss reverts. The avoid list gained stock problem framing ("a delicate balance"). In two live runs both rewrites were accepted (128 to 122 and 127 body words), about $0.10 each.

**M6R Registry of people and companies**

- Storage:
  - `Entity` (person or company), `EntityIdentifier` (kind and normalized value, unique, used for exact-match resolution), and `Sighting` (entity, source kind web, telegram or feed, `source_url`, quote, seen_at, run).
  - `Relation` (from, to, type such as works_at, founded, recruiter_for or invested_in, with evidence) and `ItemLink` (item, entity, role such as subject, contact, hiring_company, recruiter or client).
  - `MergeSuggestion` (two entities, reason, status).
  - A profile's fields are FieldValue envelopes merged from sightings.
- `EntityResolver`: exact identifiers merge automatically; a fuzzy name plus shared employer or location creates a suggestion, never a merge. Merging moves identifiers, sightings, relations and links, and is logged.
- Ingestion: scouted companies, enrichment contacts and tech decision makers (works_at), and later job postings (hiring company, recruiter) write entities, sightings and links instead of keeping people only inside item JSON.
- Privacy: everything stays local; deleting a person removes their identifiers, sightings, relations and links.
- verify: resolver tests (exact merge, fuzzy suggestion only, merge moves everything), ingestion tests from recorded scout and dossier fixtures, and a delete-person cascade test.
- Done (M6R-a and M6R-b):
  - `registry/identifiers.py` normalizes domain, email, LinkedIn and Telegram identifiers. `normalize_domain` moved here from biz discovery. It also adds a derived `name_at_company` key (normalized full name @ employer domain), so the same person found by two agents for one company is one profile.
  - `registry/registry.py` (`EntityRegistry`) provides `observe`, `attach` (merges any same-kind entity that owns an identifier), `add_sighting`, `link`, `relate`, `merge` and `delete`.
  - `registry/suggestions.py` (`MergeSuggester`) suggests companies by similar names and people by similar names plus a shared employer or location. A rejected pair is never suggested again, and accepting merges into the older entity.
  - `pipelines/biz/registry_ingest.py` turns the scouted company (domain, source pages) and the dossier's people (contacts and tech decision makers, `works_at` with title, published emails as identifiers, guessed emails only as `guessed` profile fields) into registry entries, idempotently.

**M7 Biz enrichment and letters**

- Port `outreach-enrich-company` for user-selected items: gap-fill enrichment, profiler, copywriter, naturalizer (M6), letters stored as Drafts. Sender name from project settings. `outreach-update-card` becomes a plain interaction log on Item.
- verify: fixture test from a selected item to naturalized drafts with format checks (subject only for email, salutation, sign-off with sender name, ASCII).
- Done:
  - `letters/`: `LetterFrame` writes the salutation and sign-off in Python from the project's `sender_name` and new `sender_tone` (warm, neutral or formal; DMs are always casual), so the copywriter returns only subject and body. `LetterChecker` checks subject, frame, body word budget, ASCII punctuation, markdown, and numbers missing from the evidence. Mid-sentence names missing from the evidence become review notes.
  - The `outreach-personality-profiler` and `expert-copywriter` prompts are adapted. The copywriter is cut down to short evidence-bound letters, toolless, and may use the sender's own angle claims.
  - `pipelines/biz/letter_briefs.py` gives one route per contact: own published email, then LinkedIn DM, then guessed email, then the company's shared inbox, else skipped. Hooks come from news (timely first), growth, tech and quoted fields.
  - `pipelines/biz/letters.py`: profile, then copywriter with one retry when Python checks fail, then medium naturalization, then final checks. Leftover problems stay visible in `DraftReview`, never hidden.
  - `pipelines/biz/outreach.py` (`BizOutreach`): `enrich` (selected items only) stores the dossier, registers the company and its people, and rescores. `write_letters` stores each letter as a new `Draft` version.
  - `outreach-update-card` became the `Interaction` log (`InteractionRepository`).
  - Gap-fill: enrichment first runs when the user selects an item, so there is no earlier card to fill gaps in.
  - Live result on the Katana dossier: a framed, in-budget ASCII letter with no problems, $0.13 to $0.20 per company including the voice profile. Stylometric distance to a held-out sample dropped (0.579 to 0.544, and 0.622 to 0.593 on the first run). The first run showed the copywriter refusing the sender's own figures, which the prompt fix above addressed.

**M8 Job and freelance pipelines, and source adapters**

- Split into M8a job-search (strategy with CV text from the project, sources, scouts, Python normalize/filter/FX, prescore, score, contacts, letters with naturalization), M8b freelance-search, freelance-pitch and recruiter-info, M8c `SourceAdapter` protocol plus Telegram (Telethon, user session stored outside workspaces) and ATS/feed adapters feeding scouts' candidate pools.
- Fix known contract bugs in the process: `salary_floor` units, a defined `job_slug`, excluded-reason vocabulary unified across job and freelance, consistent score shape.
- verify: per sub-milestone fixture tests plus `-m live` smoke; adapter tests against recorded HTTP and Telethon responses.
- Done (M8a job-search):
  - Python owns every hard gate and every axis: `listings.py` (canonical URL as dedup key, then a title/company/place fingerprint keeping the earliest posting), `salary.py` (range, currency, period, fixed USD table; the floor is a `Money` converted the same way), `eligibility.py` (country and region tables, the location fields before the summary, required-location phrases, work-authorization blockers), `skills.py` (word-boundary matching, short terms case-sensitive), `screening.py` (stale, industry and attribute blocklists, location, must-have gate) and `scoring/job.py` (six axes, weighted mean of assessed axes, a named deal-breaker caps the total at 40).
  - The must-have gate follows the legacy prescorer: only a listing naming none of the required skills is excluded; a partial match goes on and scores lower. The first live run with an all-must-haves gate excluded every listing.
  - The new `job-listing-judge` agent returns only the level a listing asks for, an employer signal with evidence, deal-breaker hits and a summary. `job-prescorer`, `job-scorer`, `job-letter-writer` and `job-reporter` are deleted.
  - Cover letters go through the shared `letters/composer.py` with the copywriter's new `cover_letter` channel and `sender_background` (the CV). Salutation is "Dear <name>," or "Dear Hiring Team,"; the send-to address is a published email or the apply URL, never a guessed one.
  - `pipelines/job/feeds.py` turns ATS and feed items into listings without a scout agent. LinkedIn and Indeed are kept in the canonical list but marked `scrape_allowed: false`.
  - Live result on a fictional CV: 34 feed and 3 scouted listings, 4 kept after the gates, judged, ranked, one contact found and one cover letter written with no format problems, $1.05 in total.
- Done (M8c): `sources/` with Greenhouse, Lever, Ashby, Remotive, RemoteOK, Arbeitnow and HN who-is-hiring adapters (injected `httpx` client, per-adapter cursor, errors reported not raised) and a Telethon reader. The Telegram session lives at `MOZG_TELEGRAM_SESSION` (default `~/.local/share/mozg-outreach/telegram`, 0700 directory, 0600 file), refuses to sit inside the workspaces, and is created once with `python -m mozg_outreach.sources.telegram_login`. Remotive's terms allow about four polls a day; the run scheduler must enforce it.

**M9 API**

- FastAPI: projects, briefs, field specs, sources, runs (start, cancel, resume), gate answers, items, selection, enrichment, drafts, voice samples, registry (entity profiles with sources, connections, merge suggestions, a graph endpoint scoped to a project or an entity). SSE run events with `Last-Event-ID` replay from RunEvent. One background task per run behind a semaphore.
- verify: API tests with `httpx.AsyncClient` and a fake AgentRunner covering a full biz run with a gate round trip and cancel.

**M10 UI** (stack chosen at this point)

- Projects list, brief and field editor, source picker, run view with live progress and gates, report table with per-axis score reasons and source quotes, selection and enrichment, draft editor with naturalization diff, voice samples page, person and company profiles with every source and connection, merge suggestions, and an interactive graph view of people, companies and leads.
- verify: run the app, walk a biz project end to end on a live tiny brief.

**M11 Cutover**

- Delete `legacy/`; update CLAUDE.md and README.
- Separately, in `claude-pipelines`, remove `mz-biz-outreach` and `mz-job-outreach` and the matching tests and evals, keeping `mz-creative` intact. Pushing and publishing there needs explicit approval.
- verify: `grep -r legacy/` finds nothing; full test suite green; the marketplace manifest in `claude-pipelines` no longer lists the two plugins.

## Out of scope for now

Sending, multi-user auth, hosting, Alembic migrations, PDF/DOCX CV parsing, LinkedIn or Indeed scraping.
