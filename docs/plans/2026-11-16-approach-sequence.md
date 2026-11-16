# Letters for every approach step

Asked for on 2026-11-16: the approach plan also writes the letters, one per step, because a follow-up's text depends on its place in the sequence; and the leads list shows each company's current step. Follows `2026-11-16-approach-and-browser.md` and `2026-11-16-gmail-drafts.md`.

## Decisions

- **One draft per letter touch** (email or LinkedIn); calls keep talking points. A draft carries its plan `step`, and versions count per item, channel, recipient and step. Drafts without a plan (seeker letters, biz letters when planning failed) have no step.
- **The writer knows the step**: purpose (open, follow-up, breakup), its position in the sequence, the planned talking points, and the earlier touches to the same reader, with their letter text when there is one. A reader's letters are written in step order so each follow-up sees the final text before it; different readers run in parallel. Word budgets follow the research (`docs/research/2026-11-16-outreach-messages.md`): opener 60-100, follow-up 40-110, breakup 30-70.
- **Follow-up emails reply in the same Gmail thread** (user, 2026-11-16). An email touch to an address that an earlier email touch used replies to it: no subject of its own, and Gmail Drafts gets `Re: <sent subject>` with `In-Reply-To` and `References` set to the sent message's `Message-ID`. The poller reads that header and subject when it sees the earlier step sent. Until then the follow-up can't go to Gmail, and the API says which step to send first. No fake "Re:" without a real sent message.
- **Steps are marked done by hand** (user, 2026-11-16). "Mark done" on a touch stores `{step, on, interaction_id}` in the stored approach and logs `sent` (email, LinkedIn) or `called` (phone), unless the Gmail poller already logged that step's send. Undo deletes that interaction.
- **Current step** = the first touch not done. Its due date is the first touch's done date plus the touch's day offset; before the first touch is done there is no due date. Items in lists carry it as `approach_step`.
- **Re-plan keeps done steps** (user, 2026-11-16). The planner gets them as history plus the days since the first touch, and plans only the rest, numbered after them. Python seeds the validator with the done touches, so pacing and caps count them. Once a sequence has started, re-planning reuses the stored contacts and hooks, so refs in done touches stay valid; people found by a later re-enrichment are not offered then.
- **One action**: enrichment and Re-plan both plan and then write the drafts for the steps not done. The company "Write letters" action goes; "Suggest a change" on a draft still rewrites only that step.

## Milestones

10. **Storage**: `drafts.step`; `mail_threads.step`, `sent_message_id` and `sent_subject` in place of the unread `gmail_message_id`; migration 0005; repositories key versions and threads by step. → verify: repository and API tests, migration applies to a copy of the real database.
01. **Letters per step**: letter brief gets a touch context, budgets per purpose, the writer goes step by step per reader, revision targets a step, copywriter prompt section on follow-ups and breakups, enrichment and Re-plan write drafts after planning. → verify: pipeline tests with recorded outputs for opener, follow-up in thread and breakup.
01. **Progress and re-plan**: mark-done API, `approach_step` on items, planner input with done touches, validator seeded with them, prompt update. → verify: API and planner tests.
01. **Gmail replies**: thread reads take `Message-ID` and `Subject`; the sent status stores them; a follow-up push builds the reply headers or refuses. → verify: fake IMAP tests; a live round trip the user runs.
01. **UI**: draft tabs labelled by step, Mark done on touches, step badge in the leads list, company "Write letters" removed. → verify: UI build, a look on a scratch server started outside the repo root.
