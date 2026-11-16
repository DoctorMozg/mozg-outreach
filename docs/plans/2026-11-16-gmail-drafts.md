# Gmail drafts and send tracking

Asked for on 2026-11-16 (option A of three): put email drafts into the user's Gmail Drafts over IMAP, let the user send them from Gmail, and log "sent" and "replied" on the lead by watching the mailbox. The account is Google Workspace.

## Decisions

- **The app still never sends.** It writes to the Drafts folder with IMAP `APPEND` and reads Sent Mail and All Mail. It has no SMTP code. The CLAUDE.md "No sending. Drafts only." rule stays; its wording gains "email drafts can go to Gmail Drafts".
- **Login is a Workspace app password from the environment**: `MOZG_OUTREACH_GMAIL_ADDRESS` and `MOZG_OUTREACH_GMAIL_APP_PASSWORD`, read like the Telegram credentials and never stored in the database, logged or sent to an agent. No keyring dependency. Prerequisites the user checks once: 2-Step Verification on, app passwords and IMAP allowed by the Workspace admin. Without the variables the Gmail buttons are hidden and nothing polls.
- **Standard library only**: `imaplib` over TLS to `imap.gmail.com:993`, `email.message.EmailMessage` for the draft, run in `asyncio.to_thread` because `imaplib` blocks.
- **Folders are found by their special-use flags** (`\Drafts`, `\Sent`, `\All`), not by name, since Workspace accounts in other languages rename `[Gmail]/Drafts`.
- **A pushed draft is tracked by its Gmail thread id** (`X-GM-THRID`, read back after `APPEND`). Checked on the real account (2026-11-16): sending the draft from Gmail keeps the thread and gives the message a new id. In All Mail a draft carries the `\Draft` label but not the `\Draft` flag, so both are read. Bounces landing in the same thread is still assumed.
- **What gets pushed**: only `email` drafts, with the address from the draft's `review.address`. A hand-edited version has no address of its own, so the address comes from the newest generated version with the same item, channel and recipient. A `guessed` address is pushed with the existing warning shown beside the button.
- **One Gmail draft per letter.** Pushing a newer version replaces the earlier Gmail draft while it is unsent, and the button says edits made in Gmail are lost. Once sent, the letter can't be pushed again.
- **Statuses come from the mailbox**: a message from the user's address in the thread logs "sent" (note "Sent from Gmail"); a later message from anyone else logs "replied", or "bounced" when it comes from a mailer daemon. Each is logged once per thread. Threads stop being watched 60 days after the push.
- **Polling**: a lifespan task checks every 5 minutes, plus a "Check Gmail now" button. One IMAP connection per check.

## Milestones

7. **Gmail mailbox client** (`src/mozg_outreach/mail/`): settings from the environment, a `Mailbox` protocol (push a draft, delete an unsent draft, read a thread's messages) and `GmailMailbox` on `imaplib`; MIME building; parsing of `LIST`, `APPEND` and `FETCH` responses. → verify: unit tests on captured IMAP responses and the built message; a `live` test that pushes a draft to the user's own address, reads its thread and deletes it, run only with the user's go-ahead; one manual send from Gmail to confirm the thread id holds.
1. **Push API and storage**: `mail_threads` table (draft, Gmail message and thread ids, pushed, sent, replied and bounced times) and its migration; `POST /api/drafts/{id}/gmail` with address resolution and replace-on-repush; mail status in `GET /api/items/{id}` and a `GET /api/mail` that says whether Gmail is set up. → verify: API tests with a fake mailbox, migration applies to a copy of the real database.
1. **Tracker and UI**: lifespan poller and `POST /api/mail/check`; interactions logged once per thread; "bounced" added to the interaction kinds; ItemView gets "Put in Gmail drafts" on email drafts with the draft's Gmail status, and a "Check Gmail now" button. → verify: tracker tests with the fake mailbox (sent, replied, bounced, nothing twice, 60-day cutoff), UI build, a look on the scratch server.
