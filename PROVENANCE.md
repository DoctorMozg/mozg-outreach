# Provenance

The agent prompts and reference material here come from [claude-pipelines](https://github.com/DoctorMozg/claude-pipelines), commit `695e580` (plugins v0.57.0, 2026-11-08). They were released there under the MIT License, Copyright (c) 2026 Stanislav Gonorovskii. They are relicensed here under this repository's GPL-3.0 by the same author.

| Here                                                  | Upstream                                                                |
| ----------------------------------------------------- | ----------------------------------------------------------------------- |
| `prompts/biz/*.md`                                    | `plugins/mz-biz-outreach/agents/`                                       |
| `prompts/seeker/*.md`                                 | `plugins/mz-job-outreach/agents/`                                       |
| `prompts/seeker/sources/*.json`                       | `plugins/mz-job-outreach/skills/{job-search,freelance-search}/sources/` |
| `prompts/creative/expert-{naturalizer,copywriter}.md` | `plugins/mz-creative/agents/` (forked; diverges on purpose)             |

`prompts/biz/run-summarizer.md` is adapted from upstream `outreach-reporter`. Upstream `outreach-enrichment-orchestrator` and `outreach-card-writer` were dropped: Python fans out enrichment and builds the report from the database.

The seeker strategist, source researcher, scout and judge each merge the upstream job and freelance agents of the same role, and `prompts/seeker/sources/boards.json` merges both source catalogs. `prompts/shared/seeker-run-summarizer.md` adapts `run-summarizer` to seeker leads. `prompts/seeker/telegram-post-parser.md` and `prompts/shared/{biz,seeker}-brief-writer.md` have no upstream: they were written here.

The forked `expert-copywriter` is cut down to short outreach letters; the general marketing formats (landing pages, changelogs) were dropped.

Verbatim copies of the plugins' skills and READMEs served as a porting reference during the migration. Python replaced them, and they were removed in milestone M11; git history before that commit still has them.
