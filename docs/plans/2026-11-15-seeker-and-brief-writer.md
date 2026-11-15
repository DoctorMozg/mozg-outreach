# Seeker mode, AI-written briefs and writing prompts (M20 to M29)

Follow-up to `2026-11-14-post-migration.md`. Requests from the user (2026-11-15):

- An AI-written brief: give the company's website (biz) or paste a CV (job/freelance), and the AI fills every brief field, adds extra facts and drafts report fields.
- Job and freelance searches are often for one person, so they become one mode.
- A global personal and company writing prompt: "additional prompts for personal letters (for job search) and company letters (for leads)".

## Decisions (user, 2026-11-15)

- The CV comes in as pasted text. No file upload or CV link for now, so no new dependency.
- Job and freelance merge into one `seeker` mode: one brief and CV, with switches for salaried jobs and freelance gigs, one run and one report.
- The merge follows the perfectionist route: one pipeline whose listing is a job or a gig, not the two current flows run side by side.
- The brief writer does both: extra facts in the brief, and a draft of report fields.

## Design of the seeker pipeline

Today freelance already reuses most job code: a gig is a `Listing` plus `GigTerms`, and normalization, screening, eligibility, skill matching, the skill/location/recency axes and the deal-breaker cap are shared. What stays different is data, not process: gig terms, a few judge axes and the post-search actions.

- **Listing.** `Listing.kind` is `job` or `gig`. One `Pay` model replaces `Salary` and `GigBudget` (basis hour, day, month, year or project) with one parser and one set of period factors (2080 hours and 260 days a year, 8 hours a day). Gig-only `terms` (duration, application method) stay optional on the listing. `ScoutedListing` covers both, with `company` empty when the client is hidden.
- **Strategy.** One `SeekerStrategy` from one `seeker-strategist` (opus, web): CV profile, skill rules, discipline, aliases, work-auth blockers, `job_queries` and `gig_queries`, and one scoring weight set per kind. `SeekerPreferences` merges both: remote, regions, `salary_floor` (annual) and `rate_floor` (hourly), `engagement_types`, which also cover salaried work (full-time, part-time, contract) and replace the unused `employment_type`, `vetted_only`, blocklists and deal breakers.
- **Sources.** One catalog whose boards carry `kinds` and optional guidance notes. One source-researcher call and one `seeker-scout` prompt told which kind it scouts. Feeds (ATS, HN) give jobs. The Telegram parser marks each post as a job or a gig.
- **Judge and score.** One `seeker-judge` returns one judgment shape: seniority, `counterparty` (employer or client quality, one scale), scope clarity, discipline, engagement type, deal-breaker hits and a summary. One scorer computes the same axes for both kinds (skill match, seniority fit, location, pay fit, recency, counterparty, scope clarity, discipline fit) with the kind's weights. An engagement type the user doesn't want is a blocker. Pay fit without a floor is unassessed for both kinds (jobs scored 70 before).
- **Run.** `seeker.search`: plan, approve_strategy, sources, discover_channels, approve_channels, collect (feeds, Telegram and scouts), screen (recency and limit per kind), judge, score, identify, fields, summarize. Items keep `ItemKind` JOB or GIG and one set of detail keys and report columns.
- **Actions per item kind.** Jobs: contact finder and cover letter. Gigs: proposal. They're chosen by `Item.kind`, not the project mode.
- **Code layout.** `pipelines/job` and `pipelines/freelance` become `pipelines/seeker`, and `prompts/job` becomes `prompts/seeker`. The job and freelance modes, flows, models and prompts are deleted once seeker replaces them.

## Assumptions

- For biz the brief writer's link is the user's own company site.
- For seeker the brief writer has no tools. It fills `preferences` from the CV plus an optional note, and lists every guess (remote, salary, regions) as a concern.
- The brief writer returns a draft and never saves it. Generation is one request, as the voice profile build is, not a run.
- Job and gig scopes keep their own recency defaults (7 and 30 days) and limits. The unused `*_top_n` brief fields are dropped.
- A migration converts job and freelance projects, their items and briefs to seeker. Old run states are not converted: item actions on old items need a new seeker search first. The default database holds no job or freelance data.
- The changed pay conventions and the unassessed pay axis change some existing scoring tests by design. Those test changes are called out in the commits.

## Milestones

**M20 Seeker package rename** (mechanical)

- Move `pipelines/job` into `pipelines/seeker`, `prompts/job` into `prompts/seeker` and `scoring/job.py` into `scoring/seeker.py`. No behavior change. The freelance package moves piece by piece as M21 to M23 merge it.
- verify: `uv run pytest`; pre-commit; `grep` finds no import of the old packages.

**M21 One listing and pay model**

- `Listing.kind`, `Pay` and its parser, optional gig terms on the listing, one `ScoutedListing`. `Gig`, `GigBudget`, `Salary` and `BudgetParser` go. The job and freelance flows run on the new model.
- verify: pay parsing tests for every basis; `uv run pytest`.

**M22 One judge and scorer**

- `SeekerJudgment`, the `seeker-judge` prompt, `SeekerScorer` with per-kind weights, and engagement types covering both kinds.
- verify: scorer tests per axis and kind, including the engagement blocker and unassessed pay; `uv run pytest`.

**M23 One strategy, sources and scout**

- `SeekerStrategy`, `SeekerPreferences`, the `seeker-strategist`, `seeker-source-researcher` and `seeker-scout` prompts, one board catalog with kinds, and a Telegram parser that marks gigs.
- verify: plan, sources and scout tests with recorded outputs for both kinds; `uv run pytest`.

**M24 Seeker mode and run**

- `ProjectMode.SEEKER`, `SeekerBrief{cv_text, preferences, facts, jobs, gigs}`, the `seeker.search` and `seeker.fields` flows, item actions by kind, and feeds and Telegram allowed for seeker. The job and freelance modes, flows and leftover code are deleted. An Alembic revision converts projects, briefs and items.
- verify: a run-level test with recorded outputs yields both kinds in one report and a letter for each kind; a migration test; `uv run pytest`.

**M25 Seeker UI**

- New project offers biz and seeker. The brief form shows the job and gig scopes. The report gains a Kind column and filter, and each row's action reads "cover letter" or "proposal" by kind.
- verify: `tsc`, the UI build, and a browser pass over a seeker project.

**M26 Live seeker check**

- One live seeker run with both scopes on the current boards and channels (about $4), to check the merged prompts.
- verify: both kinds are scored in one report, each with a sensible judgment, and a letter is drafted for each kind.

**M27 Brief writer**

- A `shared/brief-writer` agent takes `{mode, site_url | cv_text, note}` and returns `{brief, facts, concerns}`. Python validates the brief against the mode's model. A biz fact must cite a page the agent fetched.
- Briefs in both modes gain `facts: [{label, value, source_url}]`, which the strategists, field proposer and letter writers receive.
- `POST /api/projects/{id}/brief/generate` returns the draft, then runs the field proposer on it and saves the result as an unaccepted field version.
- verify: offline tests with recorded outputs; one live call per mode (about $0.30 each).

**M28 Brief writer UI**

- On the Brief tab, a "Write with AI" panel takes a site link (biz) or the CV (seeker) and an optional note. It fills the form without saving, shows facts as an editable list, and links to the drafted fields.
- verify: `tsc`, the UI build, and a browser pass that writes a brief for each mode.

**M29 Global writing prompts**

- Two global texts, `personal` and `company`, in their own table, edited on the Voice page. `personal` goes to cover letters and proposals, `company` to biz letters, as instructions for the copywriter and the naturalizer.
- They don't need writing samples, unlike the per-channel style prompts inside a voice profile, which stay. The letter format (salutation, sign-off, punctuation, word budget) is still enforced in Python, so a prompt can't break it.
- verify: tests that each letter kind passes the right prompt to both agents and that the format checks still apply; `tsc` and the UI build.

## Order and size

M20 to M24 build the seeker pipeline bottom-up, each leaving the current job and freelance flows working on the merged parts until M24 replaces them. M29 is independent and can move earlier if wanted. Each milestone stays within about 10 files and 400 changed lines, except the mechanical M20, ends with `uv run pytest` and pre-commit on the changed files, and is committed on its own on `feat/service-migration`.
