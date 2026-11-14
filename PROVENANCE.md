# Provenance

The agent prompts and reference material here come from [claude-pipelines](https://github.com/DoctorMozg/claude-pipelines), commit `695e580` (plugins v0.57.0, 2026-11-08). They were released there under the MIT License, Copyright (c) 2026 Stanislav Gonorovskii. They are relicensed here under this repository's GPL-3.0 by the same author.

| Here                                                  | Upstream                                                                |
| ----------------------------------------------------- | ----------------------------------------------------------------------- |
| `prompts/biz/*.md`                                    | `plugins/mz-biz-outreach/agents/`                                       |
| `prompts/job/*.md`                                    | `plugins/mz-job-outreach/agents/`                                       |
| `prompts/job/sources/*.json`                          | `plugins/mz-job-outreach/skills/{job-search,freelance-search}/sources/` |
| `prompts/creative/expert-{naturalizer,copywriter}.md` | `plugins/mz-creative/agents/` (forked; diverges on purpose)             |

The forked `expert-copywriter` is cut down to short outreach letters; the general marketing formats (landing pages, changelogs) were dropped.

Verbatim copies of the plugins' skills and READMEs served as a porting reference during the migration. Python replaced them, and they were removed in milestone M11; git history before that commit still has them.
