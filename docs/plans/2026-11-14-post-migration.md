# Post-migration plan (M12 to M16)

Follow-up to `2026-11-14-plugin-migration.md`. It closes the gaps found during the migration: schema migrations, unused biz prompts, registry ingest for job and freelance, custom report fields outside biz, and a live Telegram check.

## Decisions (user, 2026-11-14)

- Migrations use Alembic (new dependency, approved by choosing it).
- `outreach-enrichment-orchestrator` and `outreach-card-writer` are deleted; `outreach-reporter` becomes an optional run summary.
- Job and freelance leads enter the registry for every scored item, not only on enrich.
- Custom fields for job and freelance are researched for the top N items after scoring.

## Assumptions

- Biz keeps its current ingest timing (company and people on enrich). Moving biz to scoring time is a one-line change in M14 if wanted.
- No milestone below needs a schema change: summaries live in `Run.state`, fields in `Item.fields`, registry rows in existing tables. Any change that does gets an Alembic revision from M12 on.
- The run summary is biz only. Job and freelance reports have fixed axes that already explain themselves.
- Live checks cost subscription usage. Each milestone names its own.

## Milestones

**M12 Alembic** (item 4)

- Add `alembic>=1.20,<2` to dependencies. Migrations live in `src/mozg_outreach/storage/migrations/` (`env.py`, `versions/`), configured in code, so no `alembic.ini` sits at the repo root. SQLite needs `render_as_batch=True`.
- Revision `0001_baseline` creates the current schema.
- `Database.create_schema()` becomes `Database.migrate()`: a database with tables but no `alembic_version` (made by `create_all`) is stamped at the baseline, then everything upgrades to head. The API calls it on startup; tests call it in their DB fixture.
- A test runs Alembic's `compare_metadata` after upgrade and fails on any difference, which catches a model change without a migration.
- verify: `uv run pytest` green; the new tests cover a fresh DB, a `create_all` DB being stamped, and no model drift. `uv run mozg-outreach` starts on a copy of the real `~/.local/share/mozg-outreach/outreach.db` with the data intact and `alembic_version` = `0001_baseline`.

**M13 Prompt cleanup and run summary** (item 1)

- Delete `prompts/biz/outreach-enrichment-orchestrator.md` and `outreach-card-writer.md`.
- Adapt `outreach-reporter` into `prompts/biz/run-summarizer.md`: toolless, no files, no `STATUS`. Input is the strategy plus the scored items (id, title, score total, per-axis values and reasons, accepted field values, fit verdict). Output is `RunSummary {summary, patterns: [{pattern, item_ids}], recommendations, concerns}`.
- Python drops pattern entries that cite unknown item ids and adds a concern for each.
- A new `summarize` phase ends `biz.search`, controlled by `BizOptions.summary: bool = True`. A failed summary is a concern, not a failed run.
- UI: the run page shows the summary for a finished biz search.
- Update the prompt-count test (biz 15 to 13) and PROVENANCE (reporter adapted, two agents dropped).
- verify: offline test with `RecordedRunner` (summary stored, an unknown item id dropped with a concern, `summary=False` skips the phase, an agent error leaves the run complete); one live summarize call on recorded items, about $0.4.

**M14 Registry ingest for job and freelance** (item 2)

- This is the third ingest after biz, so the shared parts move to `registry/ingest.py`: observe a company with its sightings and link it to the item as `subject`; observe a person who `works_at` a company and link them as `contact`; suggest merges. `BizRegistryIngest` keeps only its biz mapping.
- Basic enrichment (user, 2026-11-14: "Basic enrichment of companies and persons should be done for all jobs/freelance opportunities. If job cannot be found/not mentioned - do not link to company."). A new `shared/lead-identifier` agent (sonnet, low effort, WebSearch/WebFetch) runs for every scored job listing and freelance gig, with a concurrency limit. Given the listing it returns `company` (name, domain, LinkedIn, sector, location, description, each with a source URL and quote) or `null` when the employer or client is not named or can't be confirmed, plus `people` named on fetched pages (hiring manager, recruiter, client contact) with title and source URL. Python drops a person without a source URL and a domain the agent didn't see on a page. A failed call is a concern and leaves the item unlinked.
- Ingest at the `enrich_leads` phase after `score`: a confirmed company becomes the item's `subject` (domain and LinkedIn are exact identifiers, so the same company merges across projects), with sightings from `listing.url` and `merged_urls` whose source kind follows the listing's sources (`feed`, `telegram` or `web`). People become `contact`s who `work_at` the company, or link to the item alone when there is no company. No company means no company entity and no company link.
- Freelance gigs whose client is the board itself (client name equals a board name) skip the company lookup but still record people named on the gig page.
- `job.enrich` (contact finder) stays the deeper step: it adds its named contact and the company LinkedIn to the same entities.
- verify: offline tests for job and freelance ingest (company with identifiers and per-kind sightings, null company leaves the item unlinked, people without a source URL dropped, a re-run reuses entities, hidden freelance client), enrich adding its contact, and the biz ingest tests passing unchanged. A live job run on a small brief shows the companies and people in the registry graph, about $3 to $5.

**M15 Custom fields for job and freelance** (item 5)

- `biz.fields` becomes one fields flow per mode. Biz keeps brief review plus proposal. Job and freelance only propose: `field-proposer` already takes `mode`, and the brief goes in without `cv_text`.
- `field-researcher` input generalizes to a `subject` (company name, domain if known, location, description) plus an optional `listing` (title, URL, summary or gig scope). Update the prompt's `## Input`.
- Job and freelance search gain the run option `research_fields_top: int = 10` (0 to 30) and a `fields` phase after `score`. It researches the accepted spec for the N best-scored items and merges the values into `Item.fields`. Without an accepted spec, the phase is skipped. No rescore: job and freelance axes don't read custom fields.
- UI: the Fields tab and the fields-done panel show for every mode; report columns come from the spec as they do in biz.
- verify: tests for proposal per mode (brief sent without `cv_text`), the top-N selection, skipping without a spec, and wrongly typed values rejected through the shared `accept_field_values`. A live job run with 4 fields and top 5, about $3 to $4.

**M16 Telegram live check** (item 3)

- Needs you: API ID and hash from my.telegram.org in `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, then an interactive login (`! uv run python -m mozg_outreach.sources.telegram_login`), plus one public job channel to read.
- Add a `live` test that reads the channel set in `TELEGRAM_LIVE_CHANNEL` through `TelethonSession` and the adapter. It checks that items come back, the cursor moves forward, a second read returns nothing new, and the session file has mode 600.
- Then run a job search through the UI with a Telegram source and fix what breaks.
- verify: `uv run pytest -m live -k telegram` passes; a job run reports Telegram items with `t.me` source URLs; under M14 their companies get `telegram` sightings.

## Order and size

M12 comes first, so any later schema change gets a migration. M13 to M15 are independent of each other. M16 waits on your credentials and can run whenever they exist. Each milestone stays within about 10 files and 400 changed lines, ends with `uv run pytest` and pre-commit on the changed files, and is committed on its own on `feat/service-migration`.
