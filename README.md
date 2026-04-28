# Job-Web-Scrapper

Pings me on Discord when new internships / co-ops / new-grad roles get posted in
SWE, Product, Tech Sales, Consulting, Investment Banking, or PE/VC.

Runs on a free GitHub Actions cron — no server, no cost.

## How it works

Every 6 hours the workflow:

1. Pulls postings from three source types:
   - **Community GitHub lists** (SimplifyJobs Summer 2026, vanshb03 Summer 2026,
     New-Grad-Positions, etc.) — strong coverage for SWE/PM/co-op internships.
   - **Greenhouse + Lever public job-board APIs** — covers tons of well-known
     tech companies (Stripe, Notion, Robinhood, Figma, Airbnb, Netflix, OpenAI,
     Anthropic, …) for sales/PM/eng roles.
   - **Watch URLs** — arbitrary company career pages, configurable in
     `config.yaml`. Useful for IB / consulting / PE pages without a JSON API.
2. Filters by role keywords (SWE, PM, sales, consulting, IB, PE…) and rejects
   postings explicitly tagged for past seasons.
3. Diffs against `seen.json` to find newly-added postings.
4. Sends a Discord embed for each new posting via webhook.
5. Commits the updated `seen.json` so the next run knows what's already been
   announced.

## Setup (one-time)

### 1. Create a Discord webhook

In your Discord server: **Server Settings → Integrations → Webhooks → New
Webhook**. Pick the channel, copy the URL.

### 2. Add the webhook as a GitHub secret

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**.

- Name: `DISCORD_WEBHOOK_URL`
- Value: the URL you just copied

### 3. Enable Actions

**Actions** tab → "I understand my workflows, go ahead and enable them" if
prompted.

### 4. First run

The first run populates `seen.json` silently (otherwise you'd get hundreds of
notifications at once). After that, you'll only be pinged for genuinely new
postings.

Trigger the first run manually: **Actions → Scrape jobs → Run workflow**. Or
just wait — the cron runs every 6 hours.

## Customizing

Edit `config.yaml`:

- `role_keywords` — what job titles to match
- `season_keywords` — which terms count as "in season"
- `github_lists` — community internship lists to watch
- `greenhouse_boards`, `lever_boards` — company slugs to monitor
- `watch_urls` — arbitrary pages to grep for matching links

Push the change; the next scheduled run picks it up.

## Running locally

```sh
pip install -r requirements.txt
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python main.py
```

Delete `seen.json` (or replace with `{}`) to reset state.
