"""Entry point. Loads config, runs all scrapers, categorizes, diffs, and notifies."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from notify import notify_discord_grouped
from scrapers import (
    Posting,
    fetch_github_lists,
    fetch_greenhouse,
    fetch_lever,
    fetch_watch_url,
    fetch_workday,
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


SENIOR_MARKERS = [
    "senior", "vice president", " vp,", " vp ", "director", "managing",
    "principal", "head of", "chief", "executive", "lead ", "staff ",
]


def categorize(
    p: Posting, cfg: dict, source_overrides: dict
) -> Optional[tuple[str, str]]:
    """Returns (posting_type_key, role_category_key) or None if filtered out."""
    title_l = p.role.lower()

    for bad in cfg.get("reject_if_title_contains", []):
        if bad.lower() in title_l:
            return None

    override = source_overrides.get(p.source) or {}

    role_cat = None
    for cat in cfg["role_categories"]:
        if any(kw.lower() in title_l for kw in cat["keywords"]):
            role_cat = cat["key"]
            break
    if role_cat is None:
        role_cat = override.get("default_role_category")
    if role_cat is None:
        return None

    posting_type = None
    for pt in cfg["posting_types"]:
        if any(ex.lower() in title_l for ex in pt.get("excludes") or []):
            continue
        if any(req.lower() in title_l for req in pt["requires_any"]):
            posting_type = pt["key"]
            break

    if posting_type is None and override.get("finance_titles"):
        if any(s in title_l for s in SENIOR_MARKERS):
            return None  # too senior for early-career bucket
        if "intern" in title_l or "summer analyst" in title_l:
            posting_type = "internship"
        elif "analyst" in title_l or "associate" in title_l:
            posting_type = "new_grad"

    if posting_type is None:
        return None

    return (posting_type, role_cat)


def main() -> int:
    cfg = load_config()
    seen = load_seen()
    first_run = not seen

    # Per-source overrides keyed by the same source label the scraper emits.
    source_overrides: dict[str, dict] = {}
    for wd in cfg.get("workday_boards") or []:
        source_overrides[f"Workday:{wd['name']}"] = {
            "default_role_category": wd.get("default_role_category"),
            "finance_titles": bool(wd.get("finance_titles")),
        }

    print(f"== Run started {datetime.now(timezone.utc).isoformat()} ==")
    print(f"   first_run={first_run}  seen={len(seen)} postings")

    # Build a flat keyword list once, for the watch_url HTML grep
    role_kws_flat = []
    for cat in cfg["role_categories"]:
        role_kws_flat.extend(kw.lower() for kw in cat["keywords"])

    all_postings: list[Posting] = []

    print("-> Community GitHub lists")
    all_postings.extend(fetch_github_lists(cfg.get("github_lists", [])))

    print("-> Greenhouse boards")
    for slug in cfg.get("greenhouse_boards", []):
        all_postings.extend(fetch_greenhouse(slug))

    print("-> Lever boards")
    for slug in cfg.get("lever_boards", []):
        all_postings.extend(fetch_lever(slug))

    print("-> Workday boards")
    for wd in cfg.get("workday_boards") or []:
        all_postings.extend(fetch_workday(wd["name"], wd["base"], wd["site"]))

    print("-> Watch URLs")
    for w in cfg.get("watch_urls") or []:
        all_postings.extend(fetch_watch_url(w["name"], w["url"], role_kws_flat))

    print(f"   fetched {len(all_postings)} raw postings")

    # Categorize and group
    groups: dict[tuple[str, str], list[Posting]] = defaultdict(list)
    seen_ids: set[str] = set()
    for p in all_postings:
        if p.id in seen_ids:
            continue
        cat = categorize(p, cfg, source_overrides)
        if cat is None:
            continue
        seen_ids.add(p.id)
        groups[cat].append(p)

    matched_total = sum(len(v) for v in groups.values())
    print(f"   {matched_total} match filters across {len(groups)} groups")

    # Diff against seen
    new_groups: dict[tuple[str, str], list[Posting]] = defaultdict(list)
    for key, postings in groups.items():
        for p in postings:
            if p.id not in seen:
                new_groups[key].append(p)
    new_total = sum(len(v) for v in new_groups.values())
    print(f"   {new_total} are new (not in seen.json)")

    # First run: silently populate seen.json, no notifications
    if first_run:
        print("   first run — populating seen.json silently, no notification sent")
        today = datetime.now(timezone.utc).date().isoformat()
        for key, postings in groups.items():
            for p in postings:
                seen[p.id] = {"first_seen": today}
        save_seen(seen)
        return 0

    if new_total > 0:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        type_meta = {pt["key"]: pt for pt in cfg["posting_types"]}
        cat_meta = {c["key"]: c for c in cfg["role_categories"]}
        type_order = [pt["key"] for pt in cfg["posting_types"]]
        cat_order = [c["key"] for c in cfg["role_categories"]]
        notify_discord_grouped(
            new_groups, type_meta, cat_meta, type_order, cat_order, webhook
        )

        today = datetime.now(timezone.utc).date().isoformat()
        for key, postings in new_groups.items():
            for p in postings:
                seen[p.id] = {
                    "first_seen": today,
                    "company": p.company,
                    "role": p.role,
                    "url": p.url,
                }
        save_seen(seen)
        print(f"   notified + saved {new_total} new postings")
    else:
        print("   nothing new")

    return 0


if __name__ == "__main__":
    sys.exit(main())
