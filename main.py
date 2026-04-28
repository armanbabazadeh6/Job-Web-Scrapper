"""Entry point. Loads config, runs all scrapers, filters, diffs, and notifies."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from notify import notify_discord
from scrapers import (
    Posting,
    fetch_github_lists,
    fetch_greenhouse,
    fetch_lever,
    fetch_watch_url,
)

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
SEEN_PATH = ROOT / "seen.json"


def load_config() -> dict:
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def save_seen(seen: dict) -> None:
    SEEN_PATH.write_text(json.dumps(seen, indent=2, sort_keys=True))


def matches_filters(p: Posting, role_kws: list[str], season_kws: list[str]) -> bool:
    title_l = p.role.lower()
    if not any(kw in title_l for kw in role_kws):
        return False
    # Season filter is permissive: if title mentions an explicit season we don't
    # want, drop it; otherwise let it through (many postings omit the season).
    explicit_other_season = any(
        s in title_l for s in ["spring 2025", "summer 2025", "fall 2025", "winter 2025"]
    )
    if explicit_other_season:
        return False
    # If user supplied season_kws, prefer matches but don't require them — just
    # boost via not-rejecting. Keeping the filter loose avoids missing untagged
    # roles. (Tighten by uncommenting below if you want strict season matching.)
    # if not any(kw in title_l for kw in season_kws):
    #     return False
    return True


def main() -> int:
    cfg = load_config()
    role_kws = [k.lower() for k in cfg.get("role_keywords", [])]
    season_kws = [k.lower() for k in cfg.get("season_keywords", [])]
    seen = load_seen()
    first_run = not seen

    print(f"== Run started {datetime.now(timezone.utc).isoformat()} ==")
    print(f"   first_run={first_run}  seen={len(seen)} postings")

    all_postings: list[Posting] = []

    print("-> Community GitHub lists")
    all_postings.extend(fetch_github_lists(cfg.get("github_lists", [])))

    print("-> Greenhouse boards")
    for slug in cfg.get("greenhouse_boards", []):
        all_postings.extend(fetch_greenhouse(slug))

    print("-> Lever boards")
    for slug in cfg.get("lever_boards", []):
        all_postings.extend(fetch_lever(slug))

    print("-> Watch URLs")
    for w in cfg.get("watch_urls") or []:
        all_postings.extend(fetch_watch_url(w["name"], w["url"], role_kws))

    print(f"   fetched {len(all_postings)} raw postings")

    # Filter
    filtered = [p for p in all_postings if matches_filters(p, role_kws, season_kws)]
    print(f"   {len(filtered)} match role/season filters")

    # Dedupe by id (different sources can list the same job)
    by_id: dict[str, Posting] = {}
    for p in filtered:
        by_id.setdefault(p.id, p)

    # Diff against seen
    new_postings = [p for pid, p in by_id.items() if pid not in seen]
    print(f"   {len(new_postings)} are new (not in seen.json)")

    # On first run, populate seen without spamming notifications
    if first_run:
        print("   first run — populating seen.json silently, no notification sent")
        for pid, p in by_id.items():
            seen[pid] = {"first_seen": datetime.now(timezone.utc).date().isoformat()}
        save_seen(seen)
        return 0

    if new_postings:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        notify_discord(new_postings, webhook)
        for p in new_postings:
            seen[p.id] = {
                "first_seen": datetime.now(timezone.utc).date().isoformat(),
                "company": p.company,
                "role": p.role,
                "url": p.url,
            }
        save_seen(seen)
        print(f"   notified + saved {len(new_postings)} new postings")
    else:
        print("   nothing new")

    return 0


if __name__ == "__main__":
    sys.exit(main())
