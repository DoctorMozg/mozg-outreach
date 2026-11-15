# UI overhaul on Mantine (M30 to M35)

Request from the user (2026-11-15): "UI needs a major overhaul, it's very not user friendly and looks really bad and outdated."

## Decisions (user, 2026-11-15)

- Stack: Mantine (`@mantine/core`, `hooks`, `notifications`) with `@tabler/icons-react` and `postcss-preset-mantine`. No Tailwind, no other kit. Versions pinned at least two weeks old (Mantine 9.7.4, icons 3.50.0).
- Scope: full redesign, flows included, not a restyle.
- No mockup: build, then judge in the browser.

## Problems seen in the old UI

- Narrow 1200px column, 14px text, low-contrast dark theme, bare browser controls, no icons or empty states.
- Project tabs give no order or status, so a biz search started without accepted fields (run 1 failed at assess).
- The report puts every field in its own column, so 24 leads become unreadable wrapped strips.
- The run page dumps dozens of concerns as one wall of text, with no phase progress.

## Design

- **Shell.** `AppShell` with a sidebar: projects (with mode badges), People and companies, Voice; a light/dark toggle; "drafts only" note. Notifications for saved, copied and started actions.
- **Project.** A header with name, mode and an actions menu (delete through a confirm modal). A setup checklist (Brief, Fields for biz, Sources, Search) shows done or blocked per step. Readiness comes from the API (`search_blocker`, from the flow's `blocker`), so the UI never re-implements the rule.
- **Run.** A stepper of the flow's phases (`RunOut.phases`), gates as cards, concerns grouped by their `Source: ` prefix and collapsed, the activity as a filtered timeline.
- **Report.** A compact table (select, score, lead, kind, date, plus picked columns, three by default, remembered per project in localStorage). Toolbar filters. A row opens a side drawer with score axes, evidence and actions. A bulk bar appears when rows are selected. Connections move to their own tab.
- **Lead.** Two columns: evidence and score on the left, actions, people and history on the right. Drafts in tabs with version select, diff views and editing.
- **Voice, Registry, Entity.** Tabs per channel, sample cards; a searchable registry table; entity profile, connections and sightings as cards.

## Milestones

1. **M30 Foundation.** Dependencies, PostCSS config, provider and theme, `AppShell`, shared components on Mantine, Projects page with a create modal. verify: `npm run build`, screenshots of the shell in light and dark.
1. **M31 Project workspace.** `search_blocker` on the project API with a test; setup checklist; Brief (schema form on Mantine inputs, Write with AI card), Fields editor, Sources cards with add modal, Runs tab. verify: `uv run pytest`, build, screenshots.
1. **M32 Run view.** `phases` on the run API with a test; stepper, gates, grouped concerns, timeline, summary. verify: tests, build, screenshot of run 1.
1. **M33 Report.** Table, column picker, filters, drawer, bulk bar, Connections tab. verify: build, screenshots with the 24 real leads.
1. **M34 Lead page.** verify: build, screenshot of a lead with drafts.
1. **M35 Voice, Registry, Entity, cleanup.** Delete the old CSS that nothing uses. verify: build, pre-commit, full test run, screenshots of every page.

Screens are checked on a scratch server (port 8743) over a copy of the user's database. No agent runs are started there.
