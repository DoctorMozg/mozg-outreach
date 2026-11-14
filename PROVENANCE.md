# Provenance

The agent prompts and reference material here come from [claude-pipelines](https://github.com/DoctorMozg/claude-pipelines), commit `695e580` (plugins v0.57.0, 2026-11-08). They were released there under the MIT License, Copyright (c) 2026 Stanislav Gonorovskii. They are relicensed here under this repository's GPL-3.0 by the same author.

| Here                                                  | Upstream                                                                                                                        |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `prompts/biz/*.md`                                    | `plugins/mz-biz-outreach/agents/`                                                                                               |
| `prompts/job/*.md`                                    | `plugins/mz-job-outreach/agents/`                                                                                               |
| `prompts/job/sources/*.json`                          | `plugins/mz-job-outreach/skills/{job-search,freelance-search}/sources/`                                                         |
| `prompts/creative/expert-{naturalizer,copywriter}.md` | `plugins/mz-creative/agents/` (forked; diverges on purpose)                                                                     |
| `legacy/biz/`, `legacy/job/`                          | skills and READMEs of both outreach plugins, verbatim                                                                           |
| `legacy/creative/`                                    | `mz-creative` naturalize skill, `scripts/forbidden-punctuation.sh`, `tests/mz_creative/test_forbidden_punctuation.py`, verbatim |

The forked `expert-copywriter` is cut down to short outreach letters; the general marketing formats (landing pages, changelogs) were dropped.

`legacy/` is a porting reference only. Python replaces it, and it is deleted at the end of the migration (milestone M11 in `docs/plans/2026-11-14-plugin-migration.md`).
