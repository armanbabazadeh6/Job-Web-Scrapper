"""Fetch postings from community GitHub lists, Greenhouse, and Lever."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, asdict
from typing import Iterable

import requests

UA = {"User-Agent": "JobWebScrapper/1.0 (+https://github.com/armanbabazadeh6)"}
TIMEOUT = 30


@dataclass(frozen=True)
class Posting:
    company: str
    role: str
    location: str
    url: str
    source: str

    @property
    def id(self) -> str:
        key = f"{self.company.lower().strip()}|{self.role.lower().strip()}|{self.url.strip()}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {**asdict(self), "id": self.id}


# ---------- GitHub community lists (markdown tables) ----------

# Matches a markdown table row: | col1 | col2 | col3 | col4 | col5 |
_ROW_RE = re.compile(r"^\|(.+)\|\s*$", re.MULTILINE)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']')
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_cell(cell: str) -> str:
    cell = cell.strip()
    # Strip HTML tags but keep their inner text
    cell = _HTML_TAG_RE.sub("", cell)
    # Convert markdown links [text](url) -> text
    cell = _MD_LINK_RE.sub(r"\1", cell)
    return cell.replace("**", "").strip()


def _extract_url(cell: str) -> str:
    m = _HREF_RE.search(cell)
    if m:
        return m.group(1)
    m = _MD_LINK_RE.search(cell)
    if m:
        return m.group(2)
    return ""


def parse_github_list(name: str, markdown: str) -> list[Posting]:
    postings: list[Posting] = []
    last_company = ""
    for match in _ROW_RE.finditer(markdown):
        raw_cells = [c for c in match.group(1).split("|")]
        # Need at least: Company | Role | Location | Link | Date
        if len(raw_cells) < 4:
            continue
        cells_clean = [_clean_cell(c) for c in raw_cells]
        # Skip header / separator rows
        joined = " ".join(cells_clean).lower()
        if "company" in cells_clean[0].lower() and "role" in joined:
            continue
        if set(cells_clean[0].replace("-", "").replace(":", "").strip()) <= {""}:
            continue
        if all(set(c.replace("-", "").replace(":", "").strip()) <= {""} for c in cells_clean):
            continue

        company = cells_clean[0]
        # SimplifyJobs uses "↳" to indicate "same as previous company"
        if company in {"↳", "⇊", "&#8627;", ""}:
            company = last_company
        else:
            last_company = company

        role = cells_clean[1] if len(cells_clean) > 1 else ""
        location = cells_clean[2] if len(cells_clean) > 2 else ""
        link_cell_raw = raw_cells[3] if len(raw_cells) > 3 else ""
        url = _extract_url(link_cell_raw)

        if not company or not role or company.lower() == "company":
            continue
        # Skip closed roles (SimplifyJobs marks them with 🔒 or "Closed")
        if "🔒" in role or "closed" in role.lower():
            continue

        postings.append(Posting(
            company=company,
            role=role,
            location=location,
            url=url or "",
            source=name,
        ))
    return postings


def fetch_github_lists(sources: Iterable[dict]) -> list[Posting]:
    out: list[Posting] = []
    for src in sources:
        try:
            r = requests.get(src["url"], headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            out.extend(parse_github_list(src["name"], r.text))
        except Exception as e:
            print(f"  ! github list failed: {src['name']}: {e}")
    return out


# ---------- Greenhouse ----------

def fetch_greenhouse(slug: str) -> list[Posting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
    except Exception as e:
        print(f"  ! greenhouse {slug}: {e}")
        return []
    out = []
    for j in jobs:
        out.append(Posting(
            company=slug.title(),
            role=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            source=f"Greenhouse:{slug}",
        ))
    return out


# ---------- Lever ----------

def fetch_lever(slug: str) -> list[Posting]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        jobs = r.json()
    except Exception as e:
        print(f"  ! lever {slug}: {e}")
        return []
    out = []
    for j in jobs:
        cats = j.get("categories", {}) or {}
        out.append(Posting(
            company=slug.title(),
            role=j.get("text", ""),
            location=cats.get("location", ""),
            url=j.get("hostedUrl", ""),
            source=f"Lever:{slug}",
        ))
    return out


# ---------- Watch URLs (best-effort HTML grep) ----------

def fetch_watch_url(name: str, url: str, role_keywords: list[str]) -> list[Posting]:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"  ! watch_url {name}: {e}")
        return []
    # Pull anchor tags whose text matches any of our keywords
    out: list[Posting] = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE):
        href, text = m.group(1), m.group(2).strip()
        text_l = text.lower()
        if any(kw in text_l for kw in role_keywords):
            full_url = href if href.startswith("http") else requests.compat.urljoin(url, href)
            out.append(Posting(
                company=name,
                role=text,
                location="",
                url=full_url,
                source=f"Watch:{name}",
            ))
    return out
