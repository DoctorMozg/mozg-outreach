# Initial research: outreach service over Claude Code plugins

Date: 2026-11-14. Inputs: 3 code investigations of `claude-pipelines` (local checkout `~/.claude/plugins/marketplaces/mozg-pipelines`, commit `695e580`, plugins v0.57.0) and 4 web research passes. Claims marked *verified* were re-checked by hand; the rest carry the researcher's confidence.

## TL;DR

1. **Drive Claude Code through `claude-agent-sdk` (Python; 0.2.182 is current on PyPI and the local CLI is 2.1.325, both verified).** `AskUserQuestion` and permission prompts arrive in the `can_use_tool` callback, which may wait indefinitely, so UI approval gates are feasible. Plain `claude -p` has no gate host and denies them.
1. **The biggest technical risk is gate delivery after subagents run.** anthropics/claude-code#76204 (*verified*: has repro, closed as stale 2026-09-01, not fixed) reports the permission stream dies with "Stream closed" after a Task subagent. Every outreach skill asks a question after dispatching subagents. Milestone 0 must prove gates work on our CLI version, comparing the SDK callback with the `--permission-prompt-tool` MCP host that `claude-pipelines/tests/e2e/gate_host.py` already uses (*verified*).
1. **Use the plugins for research and drafting; own scoring, schema, validation and naturalization checks in Python.** Upstream JSON is LLM-written with no schema validation, composite scores are LLM arithmetic, and several contracts drift between prompt, README and fixture.
1. **Naturalization needs a service-side layer.** Biz letters get naturalized without a voice profile; job letters and proposals are never naturalized. No tool builds a voice profile from samples. We build: sample store → style profile (measured) + retrieved exemplars → `expert-naturalizer` → Python validation/revert.
1. **Sources need an adapter layer.** Upstream only knows WebSearch/WebFetch. Telegram (Telethon 1.x, user session, read-only, curated channels) and API feeds (Greenhouse/Lever/Ashby, Remotive, RemoteOK, Arbeitnow, HN Algolia, Adzuna, GDELT, Companies House) are new collectors feeding a normalized item shape.
1. **No open-source project combines** both modes, AI-proposed editable report schema, Telegram sources, voice-matched naturalization and a multi-project web UI over agent pipelines. Closest stack reference: Resume-Matcher (FastAPI + SQLite + Next.js, Claude Code backend).

## Target architecture

```
Browser UI ──REST──▶ FastAPI ──▶ ProjectService ── SQLite (projects, schema versions, items, scores, drafts, run events)
    ▲                   │
    │ SSE (seq replay)  ├──▶ SourceAdapters (Telegram, ATS APIs, feeds) ──▶ items.jsonl in workspace
    │                   │
    │                   ├──▶ RunManager ── asyncio.Task per run, semaphore 2-3
    │                   │        └─ ClaudeSDKClient(cwd=workspaces/<project>, plugins=[paths],
    │                   │                           env={MZ_MEMORY_ROOT: workspace}, can_use_tool=GateBroker)
    │                   │                 └─ /mz-biz-outreach:outreach-research ... (subagents, WebSearch)
    │                   │                         └─ writes .mz/outreach/<run>/*.json
    └── POST gate answer ▶ GateBroker: dict[gate_id, Future]  ◀── can_use_tool awaits
                        │
                        └──▶ Ingest: read run JSON → validate (pydantic) → recompute scores → DB
                             Naturalize: per text field → validate → revert on damage
```

## 1. Execution layer

### SDK facts (from docs; high confidence)

- Plugins load only by local path: `plugins=[{"type": "local", "path": ...}]`. A bad path is skipped silently; assert on `SystemMessage(subtype="init").data["plugins"]` and `plugin_errors`.
- Start a skill by sending `/plugin:skill args` as the prompt.
- Isolation per project: `cwd=` workspace, `env={"MZ_MEMORY_ROOT": ...}`, `setting_sources=["project"]`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Otherwise user rules, memory and this repo's `.mz/` leak in.
- `allowed_tools` auto-approves, it doesn't restrict. Auto-approve WebSearch, WebFetch, Read, Write, Edit, Glob, Grep, Agent, Skill and the skills' Bash patterns so only `AskUserQuestion` reaches the callback.
- Streaming: `parent_tool_use_id` marks subagent messages; `TaskStarted/Progress/NotificationMessage` give progress; `ResultMessage.total_cost_usd` and `model_usage` (not `usage`, which omits subagents) give cost; `max_budget_usd` caps spend.
- Cancel with `client.interrupt()` then drain; never SIGTERM first (exit 143, broken turn). Resume with `resume=<session_id>` captured from `init`.
- Gates cannot be raised inside subagents. Forked-context skills stream late.
- Auth: with no `ANTHROPIC_API_KEY` set, the CLI uses the user's subscription login. Personal local use of the unmodified binary is permitted; a shared/hosted version needs API keys. A stray `ANTHROPIC_API_KEY` silently switches to API billing.

### Gate inventory (what the UI must answer)

| Skill                         | Gate                                                        | Mechanism                                                 | Pre-suppliable?                         |
| ----------------------------- | ----------------------------------------------------------- | --------------------------------------------------------- | --------------------------------------- |
| outreach-brief                | intake loop (offering, icp, geography, goal/channels/voice) | AskUserQuestion, ≤4 q/call, repeats                       | Seed text covering every field skips it |
| outreach-brief                | brief approval                                              | AskUserQuestion Approve/Reject, free text = feedback loop | No                                      |
| outreach-research             | empty goal                                                  | AskUserQuestion                                           | Yes, goal arg or `brief:`               |
| outreach-research             | strategy approval                                           | AskUserQuestion Approve/Reject                            | No                                      |
| outreach-enrich-company       | none, except repeated NEEDS_CONTEXT and newer state schema  | AskUserQuestion                                           | n/a                                     |
| outreach-update-card          | missing action                                              | AskUserQuestion                                           | Yes                                     |
| job-search / freelance-search | **CV path**                                                 | **prose question, not AskUserQuestion**                   | **No: ends the turn; needs resume**     |
| job-search                    | current location on NEEDS_CONTEXT                           | AskUserQuestion                                           | No                                      |
| freelance-search              | region scope                                                | AskUserQuestion                                           | No                                      |
| job-search / freelance-search | strategy approval                                           | AskUserQuestion                                           | No                                      |
| freelance-pitch               | tone; proposal review (approve / regenerate ×2 / cancel)    | AskUserQuestion                                           | No                                      |
| job-recruiter-info            | empty arg, domain NEEDS_CONTEXT                             | AskUserQuestion                                           | Mostly yes                              |

Orchestrators for brief/research/job-search/freelance-search print no `STATUS:` line. Completion is `Status:` in `.mz/task/<task>/state.md` plus files on disk.

Also unlisted in `allowed-tools`: `find` (biz relocation), `cd`/`jq >`/`grep -lP` (job-search), `for` loops (freelance-search), `echo`/`grep` (freelance-pitch). Headless runs must auto-approve these or they hit the gate callback.

## 2. Plugin data contracts: what to ingest and what to distrust

Ingest the JSON, never the markdown.

- **Biz**: `brief.json`, `climate.json`, `strategy.json`, `sources.json`, `companies/<slug>.json` (reviews, review_summary, contacts.key_people, news.items, growth, tech_profile). Card markdown sections are well-specified (`## Key Contacts`, `## Outreach Letters` with `### To <Name> (<Title>): Email`), but a fixture already deviates.
- **Job**: `search_strategy.json`, `raw_listings.json`, `scored_jobs.json` (`final_score` int 0-100, `score_breakdown` 6 axes, `score_reason`, `matched_skills`), `scored_jobs_excluded.json` (5 `excluded_reason` values), `letters/*.md` frontmatter (join on `listing_url`; `job_slug` is never defined).
- **Freelance**: `scored.json` (`score`, `axis_scores` 0-1 floats, hard-gated gigs kept at 0), `proposals/*.md` frontmatter.

Known drift to tolerate or fix upstream:

- Biz `score_breakdown` shape unspecified; README lists different scoring dimensions than the prompt; growth `Pivoting` category unmapped.
- Biz scout may emit a `_scout_notes` pseudo-row; report links break after card relocation to `active/`.
- Letters must end with the sender's first name, but nothing supplies one (fixtures invent "Sam").
- Job scorer reads `preferences.salary_floor_usd`, strategist emits `salary_floor` string; `work_auth_strict` never produced.
- Job vs freelance score shapes differ; excluded-reason vocabularies differ; the job reporter treats all exclusions as location mismatches.
- `state.md` isn't valid YAML once markdown sections are appended; three date formats are in use.
- Same-day rerun is ambiguous between resume and `_v2`.

**Scoring decision**: keep the plugin's per-axis values as features, recompute composites in Python with weights stored per project, keep `score_reason` per axis. LLM-judge position/verbosity bias is documented (arXiv 2406.07791, 2602.02219); later, calibrate weights from the user's select/reject actions.

## 3. Report schema ("AI suggests structure and fields")

- Two-stage: the LLM proposes a field spec (name, type, description, example, required) against a fixed meta-schema; the user edits; persist with version and stable field IDs. Never regenerate on later runs; only propose diffs.
- Every extracted value is an envelope `{value, source_url, quote, confidence, fetched_at}`. Verify the quote appears on the fetched page in code (LangExtract pattern) and derive displayed confidence from grounding, source agreement and age, not from model self-report.
- Contact data (emails, names) must come from a fetched page or provider response. Pattern guesses are labelled `guessed`.

## 4. Naturalization and user voice

Upstream facts (`mz-creative` v0.57.0):

- `expert-naturalizer` (sonnet, no Bash) detects English vocabulary/phrase lists, structural templates and rhythm, then rewrites in four passes. All its metrics are model estimates.
- `voice:@file:<profile>`: free-form per-register notes (length, final-period rate, commas/100 words, tone markers, stock phrases, spelling, capitalization, don't-copy list). Only example: a chat profile in `evals/mz-creative/naturalize-behavior-chat-voice/fixture.sh`.
- Defaults that hurt short messages: "cut ≥15%"; burstiness needs ~30 sentences. `outreach-enrich-company` overrides with Light severity, ±5% length, word budgets (email 120-180, DM 60-100), and a preserve list; reuse that dispatch.
- `forbidden-punctuation.sh` misses curly quotes, `-`, `--`, `...`, and banned characters inside double quotes.
- Non-English: only structural rules apply.

Service design:

1. **Voice samples**: user uploads 10-30 sent, self-written messages per genre (cold email, cover letter, DM, proposal); hold out 20% for evaluation; exclude AI-assisted text, quotes and signatures.
1. **Voice profile builder** (new): compute measurable features in Python (sentence length mean/variance, contraction rate, punctuation habits, greeting/sign-off habits) plus an LLM-written qualitative profile; render to the `voice:` file format per register. User's free-text style prompts are appended verbatim.
1. **Generation**: pass 3-5 genre-matched samples as exemplars (retrieved, not random) plus the profile. Few-shot gains saturate around 2-10 samples (arXiv 2509.14543), so the profile carries the weight.
1. **Naturalize** each text field via `expert-naturalizer` with the enrich-style dispatch, `Register:`, the profile, `preserve:` (names, numbers, CTA), `allow:` from the profile.
1. **Validate in Python**: word budget, preserved entities, salutation/sign-off, our own punctuation scan (superset of the script). Revert to the pre-naturalize snapshot on damage. Strip `[SPECIFICITY NEEDED]` markers into a review note.
1. **Evaluate** with stylometric distance to held-out samples, not AI detectors (unreliable, biased against non-native writers).
1. Log user edits to drafts as ongoing style data.

Writing practice worth encoding: short and specific beats elaborate; one verifiable hook, one proof point, one low-friction ask. Generic AI tailoring has lost signal value in hiring (arXiv 2511.08785, 2509.25054), so concrete proof matters more than personalization volume.

## 5. Data sources

Normalized item (all adapters emit this, JSONL into the run workspace):

```python
class SourceItem(BaseModel):
    source: str
    source_item_id: str        # dedup key = (source, source_item_id)
    url: str | None
    kind: Literal["job", "gig", "company", "signal"]
    title: str | None
    raw_text: str
    raw_payload: dict
    published_at: datetime | None
    fetched_at: datetime
    author: str | None
    org_name: str | None
    location: str | None
    tags: list[str] = []
    license_note: str | None   # e.g. "Remotive: attribution, 24h delay, no lead collection"
    content_hash: str
```

Each adapter: own rate limiter, stored cursor for incremental fetches.

| Source                                           | Use                 | Notes                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------ | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web search (via plugins)                         | both                | Default; upstream scouts                                                                                                                                                                                                                                                                                                                                |
| Telegram (Telethon 1.x)                          | jobs, gigs, signals | User MTProto session; bots can't read arbitrary channels. Read curated channels with `iter_messages`; `channels.searchPosts` is paid past a quota. Accounts on unofficial clients are monitored; API ToS bans scraping and AI training. Session file = full account access: store outside workspace, 0600, never in prompts. Pyrogram archived 2024-12. |
| Greenhouse / Lever / Ashby board APIs            | jobs                | Free, no auth; need per-company board tokens                                                                                                                                                                                                                                                                                                            |
| Remotive, RemoteOK, Arbeitnow                    | jobs                | Free; attribution; Remotive forbids lead collection, ~4 req/day                                                                                                                                                                                                                                                                                         |
| HN "Who is hiring" (Algolia)                     | jobs                | Free                                                                                                                                                                                                                                                                                                                                                    |
| Adzuna                                           | jobs                | Official API, key; free quota unverified                                                                                                                                                                                                                                                                                                                |
| GDELT DOC 2.0                                    | company signals     | Free, rolling 3 months                                                                                                                                                                                                                                                                                                                                  |
| Companies House                                  | UK companies        | Free key, 600 req / 5 min                                                                                                                                                                                                                                                                                                                               |
| Tavily / Exa / SerpAPI                           | search API fallback | Free tiers: 1,000 credits / $10 monthly / 250 searches                                                                                                                                                                                                                                                                                                  |
| LinkedIn, Indeed (JobSpy)                        | avoid               | ToS breach; LinkedIn returns 429 and restricts accounts                                                                                                                                                                                                                                                                                                 |
| Upwork RSS, NewsAPI free, Reddit, OpenCorporates | unavailable         | Removed 2024 / dev-only / approval-gated / non-commercial                                                                                                                                                                                                                                                                                               |

Legal: named B2B contacts are personal data under GDPR (legitimate interest needs a documented assessment). Store minimum fields with source and fetch time, set a retention TTL, support delete. Drafts-only keeps PECR exposure with the user at send time. Not legal advice.

## 6. Open-source landscape

| Project                              | Stars\* | License         | Take                                                                                           | Avoid                                         |
| ------------------------------------ | ------- | --------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------- |
| santifer/career-ops                  | ~82.5k  | MIT             | Fact-check gate blocking unsupported numbers; posting-legitimacy check; ATS APIs over scraping | Files-as-database once there's a UI           |
| MadsLorentzen/ai-job-search          | ~71.7k  | MIT             | Veto gates, language gate, `writing-style.md`, postings treated as untrusted                   | Logged-in LinkedIn access                     |
| srbhr/Resume-Matcher                 | ~29.3k  | Apache-2.0      | Closest stack: FastAPI + SQLite + Next.js, Claude Code backend, deterministic scoring          | —                                             |
| eracle/OpenOutreach                  | ~3.7k   | GPL-3.0         | Per-lead verdict with reason; confidence-gated paid enrichment                                 | —                                             |
| firecrawl/fire-enrich                | ~1.3k   | MIT             | Per-field source URLs, schema per field, SSE row streaming                                     | —                                             |
| zubair-trabzada/ai-sales-team-claude | ~1.5k   | MIT             | Explicit weighted axes shown to user                                                           | Stale                                         |
| BraaMohammed/bricks                  | 73      | none            | Recipient-persona critic loop on drafts                                                        | Stealth scraping; no license, don't copy code |
| Pickle-Pixel/ApplyPilot              | ~1.8k   | AGPL-3.0        | JSON-LD-first extraction cascade; score threshold before expensive steps                       | Autonomy; AGPL code                           |
| feder-cr/AIHawk                      | ~32.5k  | MIT (contested) | —                                                                                              | Auto-submit; LinkedIn bans                    |
| IST-DASLab/PanzaMail                 | ~304    | Apache-2.0      | Retrieve user's own past emails as exemplars                                                   | GPU fine-tuning                               |

\*Star counts from GitHub pages on 2026-11-14 via the research agent; not re-verified.

Gaps we fill: one brief for biz and job modes; AI-proposed editable schema; Telegram as a scored source; real voice matching; a multi-project web UI over agent pipelines with gates.

## 7. Upstream issues in `claude-pipelines` (separate pass, not this repo)

1. `mz-job-outreach` is not installed locally (only `mz-creative` and `mz-biz-outreach` are). *Verified.*
1. job/freelance search ask the CV path in prose, not via AskUserQuestion; no `cv:` argument.
1. No auto-approve or pre-supplied answers for strategy/brief gates; no `region:` arg for freelance-search; no `tone:` for freelance-pitch.
1. Orchestrators print no terminal `STATUS:` line.
1. Unlisted Bash commands in `allowed-tools` (`find`, `jq` redirects, `grep`, `echo`, loops).
1. Biz: `score_breakdown` shape, README scoring dimensions, `Pivoting` mapping, `_scout_notes` row, stale report links after relocation, missing sender name, README options `date:`/`outcome:` not implemented, `brief:` missing from enrich `argument-hint`.
1. Job: `salary_floor` vs `salary_floor_usd`, undefined `job_slug`, reporter mislabels exclusions, inconsistent `source_quality.json` ordering, resume vs `_v2` ambiguity, freelance temp dirs never cleaned.
1. No job-outreach letter or proposal is naturalized; `copywrite`/`document` naturalize without snapshot or punctuation check; `forbidden-punctuation.sh` blind spots.
1. No real output fixtures for any outreach skill; tests lint prompts only.

## 8. Open decisions

1. **Gate host**: SDK `can_use_tool` vs `claude -p --permission-prompt-tool` MCP host. Decide after the Milestone 0 spike.
1. **UI stack**: not chosen. Resume-Matcher's Next.js is the closest reference; a lighter option is server-rendered HTMX on the FastAPI app.
1. **Upstream changes vs wrapping**: fixing items in section 7 upstream (e.g. `cv:` argument, `STATUS` lines, JSON schemas) is cheaper than working around them here.
1. **Search API key** for adapter-side search (Tavily/Exa) or rely only on Claude Code WebSearch.

### Proposed Milestone 0 spike

Run a tiny test plugin skill that dispatches one subagent, then calls `AskUserQuestion`, via (a) `ClaudeSDKClient` + `can_use_tool` and (b) `claude -p` + MCP permission host, on CLI 2.1.325. Verify: the answer arrives in the skill, cost is reported, interrupt works, and resume after a backend restart works.
