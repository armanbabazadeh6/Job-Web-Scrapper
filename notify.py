"""Discord webhook notifier — sends sectioned messages grouped by posting type & role category."""
from __future__ import annotations

import time
from typing import Iterable

import requests

from scrapers import Posting

DEFAULT_COLOR = 0x5865F2
EMBED_DESC_LIMIT = 4000  # Discord caps at 4096; leave headroom


def _format_line(p: Posting) -> str:
    label = f"{p.company} — {p.role}"
    if p.url:
        head = f"[{label}]({p.url})"
    else:
        head = f"**{label}**"
    if p.location:
        return f"{head} · {p.location}"
    return head


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
    _post(webhook_url, {"content": f"🆕 **{total} new posting(s)** matching your filters"})

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

        lines = [_format_line(p) for p in postings]
        chunks = _chunk_lines(lines, EMBED_DESC_LIMIT)

        for i, chunk in enumerate(chunks):
            chunk_title = title if len(chunks) == 1 else f"{title} — part {i+1}/{len(chunks)}"
            embed = {
                "title": chunk_title,
                "description": "\n".join(chunk),
                "color": color,
            }
            _post(webhook_url, {"embeds": [embed]})
