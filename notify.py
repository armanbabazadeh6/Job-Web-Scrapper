"""Discord webhook notifier — one clean, readable embed per posting.

Each posting sends its own embed: clickable title, labeled metadata fields,
and an excerpt of the actual job description so nobody has to leave Discord
to know what the job is.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import requests

from scrapers import Posting

DEFAULT_COLOR = 0x5865F2

# JD excerpting: skip the company boilerplate and start at the first
# responsibilities-flavored anchor when one sits early in the text.
_SECTION_ANCHORS = [
    "what you'll do", "what you will do", "responsibilities", "you will",
    "requirements", "about the role", "the role", "key responsibilities",
    "what you'll be doing", "your impact", "day to day", "what you get to do",
]
_EXCERPT_CHARS = 1000


def _posted_ago(posted_at: Optional[datetime]) -> str:
    if posted_at is None:
        return "unknown"
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - posted_at).total_seconds() / 3600
    if hours < 0:
        hours = 0
    if hours < 1:
        return f"{max(0, int(hours * 60))}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def _ts(p: Posting) -> float:
    if p.posted_at is None:
        return 0.0
    if p.posted_at.tzinfo is None:
        p.posted_at = p.posted_at.replace(tzinfo=timezone.utc)
    return -p.posted_at.timestamp()


def _jd_excerpt(description: Optional[str], limit: int = _EXCERPT_CHARS) -> str:
    if not description:
        return ""
    text = description.strip()
    lower = text[:2500].lower()
    best = -1
    for anchor in _SECTION_ANCHORS:
        idx = lower.find(anchor)
        if idx != -1 and (best == -1 or idx < best):
            best = idx
    if best > 0:
        text = text[best:]
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def build_posting_embed(
    p: Posting, pt: dict, cat: dict, favs: Optional[list] = None
) -> dict:
    """One posting -> one Discord embed."""
    v = getattr(p, "verdict", None) or {}
    title = f"{p.company} — {p.role}".strip()
    if favs and any(f in p.company.lower() for f in favs):
        title = f"⭐ {title}"
    if getattr(p, "hotspot", False):
        title = f"📍 {title}"
    title = title[:250]

    fields: list[dict] = []
    if p.location:
        fields.append({"name": "📍 Location", "value": str(p.location)[:1024], "inline": True})
    if v.get("salary"):
        fields.append({"name": "💰 Comp", "value": str(v["salary"])[:1024], "inline": True})
    yrs = v.get("years")
    if v:
        exp = "new-grad friendly" if not yrs else f"~{yrs} yr" + ("" if yrs == 1 else "s")
        fields.append({"name": "🎓 Exp", "value": exp, "inline": True})
    fit = getattr(p, "fit", None) or v.get("fit_score")
    if fit:
        fields.append({"name": "🎯 Fit", "value": f"{fit}/5", "inline": True})
    if v.get("clearance_required"):
        fields.append({"name": "🔐 Clearance", "value": "required", "inline": True})
    fields.append({"name": "🕐 Posted", "value": _posted_ago(p.posted_at), "inline": True})

    desc_parts = []
    if v.get("verified") and v.get("reason"):
        desc_parts.append(f"*{v['reason']}*")
    excerpt = _jd_excerpt(getattr(p, "description", None))
    if excerpt:
        desc_parts.append(excerpt)
    description = "\n\n".join(desc_parts)[:4000] or "—"

    embed: dict = {
        "title": title,
        "description": description,
        "color": pt.get("color", DEFAULT_COLOR),
        "fields": fields,
        "footer": {"text": f"{pt['label']} · {cat['label']}"},
    }
    if p.url:
        embed["url"] = p.url
    if p.posted_at:
        if p.posted_at.tzinfo is None:
            embed["timestamp"] = p.posted_at.replace(tzinfo=timezone.utc).isoformat()
        else:
            embed["timestamp"] = p.posted_at.isoformat()
    return embed


def _post(webhook_url: str, payload: dict) -> None:
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code == 429:
        retry = float(resp.json().get("retry_after", 2))
        time.sleep(retry + 0.5)
        resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()
    time.sleep(0.7)  # gentle on rate limits


def notify_discord_postings(
    groups: dict[tuple[str, str], list[Posting]],
    type_meta: dict,
    cat_meta: dict,
    type_order: list[str],
    cat_order: list[str],
    webhook_url: str,
    favorites: Optional[list] = None,
) -> None:
    """Sends one embed per posting, best matches first (hot spot, then fit,
    then freshness)."""
    if not groups:
        return
    if not webhook_url:
        print("  ! DISCORD_WEBHOOK_URL not set — skipping notification")
        return

    favs = [f.lower() for f in (favorites or [])]
    flat: list[tuple[str, str, Posting]] = []
    for key in sorted(
        groups.keys(),
        key=lambda k: (type_order.index(k[0]), cat_order.index(k[1])),
    ):
        for p in sorted(
            groups[key],
            key=lambda q: (
                0 if getattr(q, "hotspot", False) else 1,
                -(getattr(q, "fit", None) or 0),
                _ts(q),
                q.company.lower(),
                q.role.lower(),
            ),
        ):
            flat.append((key[0], key[1], p))

    hot = sum(1 for _, _, p in flat if getattr(p, "hotspot", False))
    header = f"🆕 **{len(flat)}** new posting(s)"
    if hot:
        header += f" · 📍 **{hot}** in your areas"
    _post(webhook_url, {"content": header})

    for pt_key, cat_key, p in flat:
        embed = build_posting_embed(
            p, type_meta[pt_key], cat_meta[cat_key], favs
        )
        _post(webhook_url, {"embeds": [embed]})
