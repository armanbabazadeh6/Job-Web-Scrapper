# Job-Web-Scrapper

Pings me on Discord when new full-time **new-grad** roles get posted in
**Software Engineering**, **Platform/Infra Engineering**,
**Solutions/Sales Engineering**, **Product Management**, or
**Technology/Engineering Consulting**.

Runs on a free GitHub Actions cron — no server, no cost. Internships and
co-ops are rejected everywhere; this pipeline is full-time only.

## How it works

Every hour the workflow:

1. Pulls postings from three source types:
   - **Community GitHub lists** (SimplifyJobs New-Grad Positions, vanshb03
     New-Grad 2026, speedyapply 2026 SWE College Jobs).
   - **Greenhouse, Lever, and Ashby public job-board APIs** — covers
     well-known tech companies (Stripe, Airbnb, Figma, Cloudflare, OpenAI,
     Notion, Anthropic, Ramp, Vanta, Cursor, Palantir, …). Ashby and Lever
     responses include the full job description.
2. Filters by role keywords (SWE, platform/infra, solutions/sales engineer)
   and rejects internships/co-ops, obviously senior titles
   (senior/staff/principal/lead/…), past-season postings, and non-US roles.
3. Sends titles that explicitly say "new grad" / "graduate" / "campus" /
   "class of 2026" straight to Discord — no AI needed.
4. Sends ambiguous titles ("Solutions Engineer", "Software Engineer I")
   through the **entry-level verifier**:
   - fetches the full job description (per-job APIs for Greenhouse/Workday,
     embedded for Ashby/Lever)
   - a regex screen hard-rejects descriptions explicitly demanding lots of
     experience — free, no API calls
   - the rest are classified by an LLM (Gemini free tier by default) which
     reads the JD and returns `years_required` / `entry_level` / `seniority`
   - only postings that pass get pinged, annotated like
     "✅ AI-verified · ~0 yrs exp"
5. Diffs against `seen.json` (already-announced postings) and `verdicts.json`
   (already-classified postings) so nothing is ever processed twice.
6. Sends a Discord embed per new posting group via webhook, then commits the
   updated state files.

If the LLM API is down or the key is missing, the verifier degrades to the
regex screen so notifications never stop (postings get "⚠️ not AI-verified").

## Setup (one-time)

### 1. Create a Discord webhook

In your Discord server: **Server Settings → Integrations → Webhooks → New
Webhook**. Pick the channel, copy the URL.

### 2. Add the secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**.

- Name: `DISCORD_WEBHOOK_URL` — value: the webhook URL
- Name: `LLM_API_KEY` — value: a Gemini API key from
  <https://aistudio.google.com> (free, no credit card). Any
  OpenAI-compatible endpoint works — see the `llm:` section of
  `config.yaml` for alternatives (OpenCode Zen, Groq, …).

### 3. Enable Actions

**Actions** tab → "I understand my workflows, go ahead and enable them" if
prompted.

### 4. First run

The first run populates `seen.json` silently (otherwise you'd get hundreds of
notifications at once). After that, you'll only be pinged for genuinely new
postings.

Trigger the first run manually: **Actions → Scrape jobs → Run workflow**. Or
just wait — the cron runs hourly.

## Customizing

Edit `config.yaml`:

- `role_categories` — what job titles to match
- `greenhouse_boards`, `lever_boards`, `ashby_boards` — company slugs to
  monitor (Ashby boards need zero extra API calls for verification)
- `llm.model` / `llm.base_url` — which LLM endpoint verifies postings
- `llm.max_years` — the experience ceiling for a posting to pass
- `llm.fallback` — `lenient` (keep unverified postings) or `strict` (drop)

Push the change; the next scheduled run picks it up.

## Running locally

```sh
pip install -r requirements.txt
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." \
LLM_API_KEY="..." python main.py
```

Both env vars are optional — without the webhook nothing is sent, and
without the API key the verifier runs in regex-fallback mode.

Delete `seen.json` (or replace with `{}`) to reset state.
