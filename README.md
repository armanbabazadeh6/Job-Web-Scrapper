# Job-Web-Scrapper

<!-- After your first run, drop a screenshot of a Discord ping here:
     ![ping](assets/discord-ping.png) — it sells the project better than any paragraph. -->

New-grad tech jobs, straight to your Discord.

Every hour, this pipeline scans 40+ company job boards and community lists, has an AI read the full description of every posting that looks like a fit, and pings you when something genuinely entry-level posts. It runs on GitHub's free tier. Total cost: $0.

What it watches:

- **Software / Platform / Infrastructure Engineering**
- **Solutions & Sales Engineering** (pre-sales, technical)
- **Product Management**
- **Technology / Engineering Consulting**

Full-time only. Internships and co-ops get rejected before they reach you.

## Run your own

Four steps, about five minutes:

1. **Fork the repo** (button, top right of this page).
2. **Add two secrets** in your fork: *Settings → Secrets and variables → Actions*
   - `DISCORD_WEBHOOK_URL`: a webhook from your Discord server (*Server Settings → Integrations → Webhooks*)
   - `LLM_API_KEY`: a free Gemini key from [aistudio.google.com](https://aistudio.google.com), no credit card needed
3. **Enable Actions** in your fork's Actions tab if it prompts you.
4. **Trigger the first run**: *Actions → Scrape jobs → Run workflow*.

The first run stays quiet while it records what's already posted. After that, new postings ping you as they appear. Cost: the cron, the job boards, and the AI all have free tiers that dwarf this workload. Nothing asks for a card.

## How it works

1. Pulls postings hourly from 40+ boards via the Greenhouse, Lever, Ashby, and Workday APIs (Stripe, OpenAI, Notion, Figma, Cloudflare, Ramp, Palantir, PwC, and more), plus community new-grad lists.
2. Filters by role keywords, drops internships, senior titles, non-US roles, and anything older than a week.
3. Titles that say "new grad" or "graduate" go straight to Discord. Ambiguous titles ("Solutions Engineer", "Software Engineer I") go to an LLM, which reads the full job description and only passes postings asking for at most a couple years of experience. Survivors carry a "✅ AI-verified" tag.
4. Commits state back (`seen.json`, `verdicts.json`) so nothing gets announced or AI-checked twice.

If the AI endpoint is down, verification falls back to a regex screen so pings keep coming (tagged "⚠️ not AI-verified").

## Customizing

Edit `config.yaml`:

- `role_categories` — which job titles match
- `locations` — hot-spot areas get a 📍 tag and sort first (`mode: prefer` or `only`)
- `greenhouse_boards`, `ashby_boards`, `lever_boards`, `workday_boards` — company slugs to watch
- `llm.model` — which LLM verifies postings (a list, tried in order)
- `llm.max_years` — the experience ceiling for a posting to pass

Push the change; the next run picks it up.

## Running locally

```sh
pip install -r requirements.txt
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." \
LLM_API_KEY="..." python main.py
```

Both env vars are optional; without the key, verification runs in regex-fallback mode. Delete `seen.json` (or replace with `{}`) to reset state.
