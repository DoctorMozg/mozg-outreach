# Research changes: sequence, letters, sending

Implements items 1-12 from [the 2026-11-17 research](../research/2026-11-17-approach-and-letters.md). The user's decisions (2026-11-17):

- the opt-out line goes on every first email to each person, in every region; Gulf companies keep it on every letter;
- 3 draft candidates per letter, picked by a separate judge;
- the signature shows "Founder", the company, the site, LinkedIn and the phone.

After the build, the user restarts the server on 8742, and the biz project's settings get the new values through the API.

## Milestones

Each milestone ends with `uv run pytest -q`, pre-commit on its changed files, and `npm run build` when the UI changed.

### M1. Sequence state: stop, pause, recycle (items 1, 9, 10)

- `ApproachStep` gains:
  - `state` (`active`, `paused`, `stopped`, `done`);
  - `reason`, e.g. "replied on 2026-11-20";
  - `resume_on`;
  - `recycle_on`.
- State comes from the item's interactions since the first touch:
  - `replied`, `refused`, `declined`, `opted_out`, `not_now` and `bounced` stop the sequence;
  - `out_of_office` pauses it until `paused_until`, and due dates shift to 2 business days after.
- New interaction kinds: `out_of_office`, `not_now`, `opted_out`. Logging `out_of_office` or `not_now` takes an optional date: the return date, or the date they said to come back.
- `recycle_on`: the `not_now` date, else 90 days after the last touch of a finished, silent sequence.
- UI: the step badge shows stopped, paused and "re-plan due". The log menu takes the date. The list can filter "re-plan due".
- verify: tests for each stop kind, the pause shifting due dates, and both recycle dates; the API returns the state in list and detail.

### M2. Planner rules and recycling (items 2, 9)

- Validator:
  - email at most 2 people per company, of any size; a third person only by LinkedIn or phone;
  - at most 4 emails per person;
  - at most 2 calls per person;
  - a call never ends the sequence, and needs an email to the same person on the same day or the next.
- A recycled plan (the previous sequence finished or stopped with `not_now`):
  - moves the old one into `history`;
  - plans at most 3 touches;
  - the prompt asks for a new angle, and a new contact when one exists.
- Hooks carry their `date`. The prompt opens on a trigger only when it is 90 days old or less, or a job post that is still open.
- Other prompt rules:
  - 3 emails per person, a 4th only with something new to give;
  - the strongest proof in touch 3;
  - a LinkedIn note only for a real reason;
  - the second person's letter names the first contact openly. `TouchContext.others_contacted` carries this to the copywriter.
- verify: validator tests for each new drop, a recycle test, prompt tests still passing.

### M3. Sender card, things to send, opt-out (items 3, 4, 6)

- `BizBrief.sender`: title, company, website, LinkedIn, phone, postal address, privacy URL.
- `Offering.assets`: things the sender can send, each with name, about and URL.
- `LetterFrame.card`: appended under the sign-off on email, after naturalizing. The opt-out goes on the first email to each person, and on every letter to a Gulf company. EU and UK first emails get a privacy line when the brief has a privacy URL.
- `LetterBrief.offers` and `ApproachInput.offer.assets`: the copywriter and the planner offer only these, or "a short note on how we'd approach X".
- A German company gets a concern: §7 UWG needs prior consent.
- verify: frame tests for the card, the opt-out on the first email only and the EU privacy line; a letter test shows offers reaching the brief.

### M4. Letter checks (item 5)

- Hard checks: email and DM letters must pass these, and the copywriter retries on a failure.
  - subject of 2-4 words, with no "?" or digits;
  - a first sentence of 20 words or fewer that doesn't start with "I", "We", "My name", "I hope", "I came across" or "Reaching out";
  - exactly one question in an opener;
  - no meeting or call ask on touch 1;
  - none of the bump phrases in a follow-up.
- Warnings, as review notes:
  - Flesch-Kincaid grade above 7, from a syllable heuristic with no new dependency;
  - a sentence over 22 words;
  - more sender pronouns than reader pronouns;
  - more than 2 verified facts;
  - enthusiasm words.
- Prompt: the subject rules, the preview rule, and the capability told as what peers did.
- verify: one test per check, a passing and a failing case each.

### M5. Guarded sending and auto-replies (items 7, 10)

- Migration 0007:
  - a `suppressions` table: address, domain, reason, item, created;
  - `mail_threads.auto_replied_at` and `mail_threads.sent_body`.
- Logging `opted_out` or `refused` suppresses the person's addresses, and a bounce suppresses the bounced address. The block applies in every project:
  - the planner drops suppressed routes;
  - a push to Gmail answers 409.
- After a bounce, guessed addresses on that domain are dropped.
- Pushing a guessed address needs `confirm=true`. At most 3 guessed pushes a day.
- The push answer warns when the day's pushes are over twice the 7-day average.
- The Gmail poller reads `Auto-Submitted` and `X-Autoreply`, plus auto-reply subjects. It logs `out_of_office` instead of `replied` and keeps watching the thread.
- verify: tests for suppression on each path, the guessed confirm and cap, and auto-reply parsing; the migration runs on a copy of the real DB.

### M6. Sameness (item 8)

- `LetterBrief.avoid_phrasings` gets the openers and asks of the project's last 30 letters to other companies, and the prompt says not to echo them.
- A review note flags a final opener or ask that is too similar (difflib ratio of 0.75 or more) and names the company.
- verify: a test with a near-duplicate opener.

### M7. Candidates and judge (item 11)

- The composer drafts 3 candidates in parallel. Each has its own variant directive (observation, problem, peer story) and its own retry.
- A `letter-judge` prompt (sonnet) scores candidates on fixed axes:
  - hook relevance;
  - specificity;
  - one clear ask;
  - reader effort;
  - sounds like the sender;
  - no AI tells.
- Two judge calls see the candidates in rotated order. Python sums the axes and picks; candidates with hard problems lose to clean ones.
- Only the winner is naturalized. The review keeps the scores and the losing drafts.
- verify: composer tests with recorded candidates and judge output, including the position-swap sum.

### M8. Learning loop (item 12)

- When the poller sees a send, it stores the sent body (`text/plain` part).
- The draft view shows how much of the draft was edited.
- `GET /api/projects/{id}/outcomes` gives, per angle and per touch purpose: sent, replied, and a Beta(1,1) posterior mean with a 90% interval. The project page shows the table.
- verify: tests on the edit ratio, the outcome counts and the interval; a UI build.

### M9. Docs and settings

- README workflow notes.
- After the user restarts 8742, the biz project gets these through the API:
  - `sender_name` "Stanislav Gonorovskii";
  - the sender card;
  - assets from sgon.ai: the guides, the readiness check, the live demo and the case studies.
