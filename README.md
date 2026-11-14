# mozg-outreach

A local web service for outreach: find client companies (biz), job openings (job) or freelance gigs (freelance), score them, enrich the ones you pick, and get draft letters in your own voice. It never sends anything. Drafts only.

- **Projects** hold a brief. For biz projects the AI proposes the report fields from the brief, and you edit and accept them.
- **Runs** search your chosen sources. A run pauses at a strategy approval gate, so you can edit the plan before the search starts. Scores are computed in Python from per-axis features, each with a reason.
- **Leads** you select can be enriched (biz and job) and turned into letters, cover letters or proposals. Every draft is rewritten toward your voice, built from your writing samples and style rules.
- **Registry**: people and companies from all projects live in one global registry with profiles, the sources each was seen in, merge suggestions and a graph.
- **AI steps** run on your locally installed Claude Code through the Claude Agent SDK, using your subscription login. There is no Anthropic API key.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js and npm, to build the UI
- Claude Code, installed and logged in

## Quick start

```bash
uv sync
npm --prefix ui ci --ignore-scripts
npm --prefix ui run build
uv run mozg-outreach
```

Open <http://127.0.0.1:8742>. The server binds to localhost only and has no auth, because it drives your Claude Code login. If `ui/dist` is missing, the API starts without the UI.

## A typical biz workflow

1. Open **Projects**, create a project in `biz` mode and fill in the brief.
1. On the **Fields** tab, propose fields, edit them and accept the version. Biz projects only.
1. On the **Sources** tab, check that web search is listed (every project gets it by default).
1. On **Runs**, start a run. When it shows `awaiting_gate`, review the strategy, edit it if needed and approve.
1. When the run finishes, open the **Report** tab. Leads are scored, and each field links to its source and a quote.
1. Open a lead, tick **selected**, then **Enrich** and **Write letters**. The first pass is a draft you can edit and save.
1. Browse **Registry** for people and companies, their sources, merge suggestions and the graph.

Before step 6, open **Voice** and paste messages you wrote yourself, per channel (`email`, `dm`, `cover_letter`, `proposal`). Add style rules, one per line, and build the profile. Your rules win over inferred style.

Job projects work the same way without the fields step. Freelance projects have no enrich action: you go from selected lead to proposal.

## Data sources

Web search is available to every mode: agents search and read the web. Feed adapters serve job projects only. Kinds registered in `src/mozg_outreach/sources/factory.py`:

`greenhouse`, `lever`, `ashby`, `remotive`, `remoteok`, `arbeitnow`, `hn_whoishiring`, `telegram`

Add a source on the **Sources** tab by picking a kind and editing its JSON config.

### Telegram

Telegram reads channels you list in the config, using your own account.

```bash
export TELEGRAM_API_ID=...
export TELEGRAM_API_HASH=...
uv run python -m mozg_outreach.sources.telegram_login
```

The login prompts for your phone number, code and 2FA password, then saves a session file. The default location is `~/.local/share/mozg-outreach/telegram.session`. Override the base path with `MOZG_TELEGRAM_SESSION`. The session file grants full access to your account. Keep it private, outside any workspace, and never paste it into a prompt. Telegram's API terms forbid scraping for AI training.

## Configuration

| Variable                               | Default                                 | Meaning                                                     |
| -------------------------------------- | --------------------------------------- | ----------------------------------------------------------- |
| `MOZG_OUTREACH_HOME`                   | `~/.local/share/mozg-outreach`          | Data directory: `outreach.db` and per-project `workspaces/` |
| `MOZG_OUTREACH_PORT`                   | `8742`                                  | Port on `127.0.0.1`                                         |
| `MOZG_OUTREACH_MAX_RUNS`               | `2`                                     | Runs that may execute in parallel                           |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | none                                    | Telegram API credentials                                    |
| `MOZG_TELEGRAM_SESSION`                | `~/.local/share/mozg-outreach/telegram` | Telegram session file base path                             |

## Cost

Every agent step uses Claude Code subscription usage. The run page shows the cost of each run in USD. Live tests (below) use it too.

## Development

```bash
uv run pytest                  # offline; replays recorded agent outputs
uv run pytest -m live          # real Claude Code and web calls, costs usage
pre-commit run --files <files> # ruff, ruff-format, mdformat, mypy --strict, ui typecheck
npm --prefix ui run dev        # Vite on :5173, proxies /api to 127.0.0.1:8742
```

Run the Python service in another terminal for the dev server. Agent prompts live in `prompts/`, and `CLAUDE.md` explains the architecture decisions.

## License and origin

GPL-3.0, see `LICENSE`. The agent prompts come from the `mz-biz-outreach`, `mz-job-outreach` and `mz-creative` plugins in [claude-pipelines](https://github.com/DoctorMozg/claude-pipelines), relicensed here by the same author. Details are in `PROVENANCE.md`.
