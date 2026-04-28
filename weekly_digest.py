"""Weekly velocity digest. Posts to Discord:
  - Hiring spree: companies with ≥10 postings this week, sorted by week-over-week ratio
  - Freeze signal: companies that posted regularly but have gone quiet for 30d
  - Top categories this week

Reads from seen.json. Treats `posted_at` as the source of truth when present,
otherwise falls back to `first_seen` (when WE first noticed the posting).

Run manually: `python weekly_digest.py`
Or via the workflow: .github/workflows/weekly_digest.yml
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
SEEN_PATH = ROOT / "seen.json"

SPIKE_MIN_THIS_WEEK = 10        # at least N postings this week to count as spike
FREEZE_MIN_PRIOR = 5            # had at least N postings in their history
FREEZE_QUIET_DAYS = 30          # ...and haven't posted in this many days


def _entry_date(entry: dict) -> date | None:
    s = entry.get("posted_at") or entry.get("first_seen")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def compute_digest(seen: dict, today: date) -> dict:
    week_ago = today - timedelta(days=7)
    two_weeks_ago = today - timedelta(days=14)

    this_week: Counter = Counter()
    prev_week: Counter = Counter()
    historical: Counter = Counter()
    last_seen: dict[str, date] = {}
    cat_this_week: Counter = Counter()
    type_this_week: Counter = Counter()

    for entry in seen.values():
        if not isinstance(entry, dict):
            continue
        company = entry.get("company")
        if not company:
            continue
        d = _entry_date(entry)
        if d is None:
            continue
        historical[company] += 1
        if company not in last_seen or d > last_seen[company]:
            last_seen[company] = d
        if d >= week_ago:
            this_week[company] += 1
            if cat := entry.get("role_category"):
                cat_this_week[cat] += 1
            if pt := entry.get("posting_type"):
                type_this_week[pt] += 1
        if two_weeks_ago <= d < week_ago:
            prev_week[company] += 1

    spikes = []
    for company, current in this_week.items():
        if current < SPIKE_MIN_THIS_WEEK:
            continue
        prev = prev_week.get(company, 0)
        ratio = current / prev if prev > 0 else float("inf")
        spikes.append((company, current, prev, ratio))
    spikes.sort(key=lambda x: (x[3], x[1]), reverse=True)

    freezes = []
    for company, latest in last_seen.items():
        if historical[company] < FREEZE_MIN_PRIOR:
            continue
        days_quiet = (today - latest).days
        if days_quiet >= FREEZE_QUIET_DAYS:
            freezes.append((company, days_quiet, historical[company]))
    freezes.sort(key=lambda x: x[1], reverse=True)

    return {
        "today": today.isoformat(),
        "this_week_total": sum(this_week.values()),
        "prev_week_total": sum(prev_week.values()),
        "spikes": spikes[:15],
        "freezes": freezes[:15],
        "top_companies": this_week.most_common(10),
        "top_categories": cat_this_week.most_common(8),
        "top_types": type_this_week.most_common(),
    }


def render_embeds(d: dict, cat_labels: dict, type_labels: dict) -> list[dict]:
    embeds = []

    # Header / summary
    delta = d["this_week_total"] - d["prev_week_total"]
    delta_str = f"(+{delta})" if delta > 0 else f"({delta})" if delta < 0 else "(±0)"
    desc_lines = [
        f"**{d['this_week_total']}** new postings this week {delta_str}",
    ]
    if d["top_categories"]:
        cat_summary = " · ".join(
            f"{cat_labels.get(k, k)} {v}" for k, v in d["top_categories"][:4]
        )
        desc_lines.append(f"\n{cat_summary}")
    if d["top_types"]:
        type_summary = " · ".join(
            f"{type_labels.get(k, k)} {v}" for k, v in d["top_types"]
        )
        desc_lines.append(f"\n{type_summary}")

    embeds.append({
        "title": f"📊 Weekly Movers Digest — {d['today']}",
        "description": "\n".join(desc_lines),
        "color": 0x9B59B6,
    })

    # Hiring spree
    if d["spikes"]:
        lines = []
        for company, current, prev, ratio in d["spikes"]:
            ratio_str = "∞" if ratio == float("inf") else f"{ratio:.1f}×"
            lines.append(f"**{company}** — {current} this wk · {prev} last wk · {ratio_str}")
        embeds.append({
            "title": "🚀 Hiring Spree (≥10 postings this week)",
            "description": "\n".join(lines)[:4000],
            "color": 0x2ECC71,
        })
    else:
        embeds.append({
            "title": "🚀 Hiring Spree",
            "description": "_No companies hit the 10+ posting threshold this week._",
            "color": 0x2ECC71,
        })

    # Freezes
    if d["freezes"]:
        lines = []
        for company, days_quiet, total in d["freezes"]:
            lines.append(f"**{company}** — silent {days_quiet} days · {total} posts in history")
        embeds.append({
            "title": "🧊 Hiring Freeze Signal (30+ days quiet, used to post regularly)",
            "description": "\n".join(lines)[:4000],
            "color": 0x3498DB,
        })

    # Top companies this week
    if d["top_companies"]:
        lines = [f"**{c}** — {n}" for c, n in d["top_companies"]]
        embeds.append({
            "title": "🏆 Most Active This Week",
            "description": "\n".join(lines)[:4000],
            "color": 0xF39C12,
        })

    return embeds


def post_to_discord(embeds: list[dict], webhook_url: str) -> None:
    if not webhook_url:
        print("  ! DISCORD_WEBHOOK_URL not set — printing digest instead")
        for e in embeds:
            print(f"\n=== {e['title']} ===\n{e['description']}")
        return
    # Discord caps at 10 embeds per message
    for i in range(0, len(embeds), 10):
        batch = embeds[i:i + 10]
        r = requests.post(webhook_url, json={"embeds": batch}, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            r = requests.post(webhook_url, json={"embeds": batch}, timeout=30)
        r.raise_for_status()
        time.sleep(0.7)


def load_label_maps() -> tuple[dict, dict]:
    """Read labels from config.yaml so emojis match the main scraper."""
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
        cat_labels = {c["key"]: c["label"] for c in cfg.get("role_categories", [])}
        type_labels = {pt["key"]: pt["label"] for pt in cfg.get("posting_types", [])}
        return cat_labels, type_labels
    except Exception:
        return {}, {}


def main() -> int:
    if not SEEN_PATH.exists():
        print("seen.json missing — nothing to digest yet")
        return 0
    seen = json.loads(SEEN_PATH.read_text() or "{}")
    if not seen:
        print("seen.json empty — skipping digest")
        return 0
    cat_labels, type_labels = load_label_maps()
    today = datetime.now(timezone.utc).date()
    digest = compute_digest(seen, today)
    embeds = render_embeds(digest, cat_labels, type_labels)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    post_to_discord(embeds, webhook)
    print(f"posted weekly digest: spikes={len(digest['spikes'])}, freezes={len(digest['freezes'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
