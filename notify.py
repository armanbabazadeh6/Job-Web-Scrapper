"""Discord webhook notifier — sends sectioned messages grouped by posting type & role category."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

from scrapers import Posting

DEFAULT_COLOR = 0x5865F2
EMBED_DESC_LIMIT = 4000  # Discord caps at 4096; leave headroom

HOT_24H = "🔥🔥 "
HOT_48H = "🔥 "


def _hot_badge(posted_at: Optional[datetime]) -> str:
    if posted_at is None:
        return ""
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - posted_at).total_seconds() / 3600
    if hours < 0:
        hours = 0
    if hours <= 24:
        return HOT_24H
    if hours <= 48:
        return HOT_48H
    return ""


def _format_line(p: Posting) -> str:
    badge = _hot_badge(p.posted_at)
    label = f"{p.company} — {p.role}"
    head = f"[{label}]({p.url})" if p.url else f"**{label}**"
    line = f"{badge}{head}"
    if p.location:
        line += f" · {p.location}"
    return line


def _chunk_lines(lines: list[str], max_chars: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current and current_len + line_len > max_chars:
            chunks.append(current)
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append(current)
    return chunks


def _post(webhook_url: str, payload: dict) -> None:
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code == 429:
        retry = float(resp.json().get("retry_after", 2))
        time.sleep(retry + 0.5)
        resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()
    time.sleep(0.7)  # gentle on rate limit


def notify_discord_grouped(
    groups: dict[tuple[str, str], list[Posting]],
    type_meta: dict,
    cat_meta: dict,
    type_order: list[str],
    cat_order: list[str],
    webhook_url: str,
) -> None:
    if not groups:
        return
    if not webhook_url:
        print("  ! DISCORD_WEBHOOK_URL not set — skipping notification")
        return

    total = sum(len(v) for v in groups.values())
    hot_count = sum(
        1 for ps in groups.values() for p in ps if _hot_badge(p.posted_at)
    )
    header = f"🆕 **{total} new posting(s)** matching your filters"
    if hot_count:
        header += f"  ·  🔥 **{hot_count}** posted in the last 48h"
    _post(webhook_url, {"content": header})

    sorted_keys = sorted(
        groups.keys(),
        key=lambda k: (type_order.index(k[0]), cat_order.index(k[1])),
    )

    for (pt_key, cat_key) in sorted_keys:
        postings = groups[(pt_key, cat_key)]
        pt = type_meta[pt_key]
        cat = cat_meta[cat_key]
        title = f"{pt['label']} — {cat['label']} ({len(postings)})"
        color = pt.get("color", DEFAULT_COLOR)

        def _sort_key(p: Posting) -> tuple:
            b = _hot_badge(p.posted_at)
            rank = 0 if b == HOT_24H else (1 if b == HOT_48H else 2)
            return (rank, p.company.lower(), p.role.lower())

        postings_sorted = sorted(postings, key=_sort_key)
        lines = [_format_line(p) for p in postings_sorted]
        chunks = _chunk_lines(lines, EMBED_DESC_LIMIT)

        for i, chunk in enumerate(chunks):
            chunk_title = title if len(chunks) == 1 else f"{title} — part {i+1}/{len(chunks)}"
            embed = {
                "title": chunk_title,
                "description": "\n".join(chunk),
                "color": color,
            }
            _post(webhook_url, {"embeds": [embed]})
