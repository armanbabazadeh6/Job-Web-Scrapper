"""Discord webhook notifier. Batches postings into embeds (max 10 per message)."""
from __future__ import annotations

import os
import time
from typing import Iterable

import requests

from scrapers import Posting

DISCORD_COLOR = 0x5865F2
MAX_EMBEDS_PER_MESSAGE = 10
MAX_FIELD_LEN = 256


def _truncate(s: str, n: int = MAX_FIELD_LEN) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _embed(p: Posting) -> dict:
    desc_parts = []
    if p.location:
        desc_parts.append(f"📍 {_truncate(p.location, 200)}")
    desc_parts.append(f"_{p.source}_")
    return {
        "title": _truncate(f"{p.company} — {p.role}"),
        "url": p.url or None,
        "description": "\n".join(desc_parts),
        "color": DISCORD_COLOR,
    }


def notify_discord(postings: Iterable[Posting], webhook_url: str) -> None:
    postings = list(postings)
    if not postings:
        return
    if not webhook_url:
        print("  ! DISCORD_WEBHOOK_URL not set — skipping notification")
        return

    # First message: header
    header = {"content": f"**🆕 {len(postings)} new posting(s)** matching your filters"}
    requests.post(webhook_url, json=header, timeout=30).raise_for_status()
    time.sleep(0.5)

    for i in range(0, len(postings), MAX_EMBEDS_PER_MESSAGE):
        batch = postings[i : i + MAX_EMBEDS_PER_MESSAGE]
        payload = {"embeds": [_embed(p) for p in batch]}
        resp = requests.post(webhook_url, json=payload, timeout=30)
        if resp.status_code == 429:
            retry = float(resp.json().get("retry_after", 2))
            time.sleep(retry + 0.5)
            resp = requests.post(webhook_url, json=payload, timeout=30)
        resp.raise_for_status()
        time.sleep(0.7)  # gentle on rate limit
