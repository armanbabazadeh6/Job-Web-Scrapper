"""Fetch postings from community GitHub lists, Greenhouse, Lever, and Workday."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

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
    posted_at: Optional[datetime] = None  # when the company posted it (UTC)

    @property
    def id(self) -> str:
        key = f"{self.company.lower().strip()}|{self.role.lower().strip()}|{self.url.strip()}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = {**asdict(self), "id": self.id}
        if self.posted_at:
            d["posted_at"] = self.posted_at.isoformat()
        return d


# ---------- Date parsing helpers ----------

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_epoch_ms(ms: Optional[int]) -> Optional[datetime]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _parse_workday_posted(s: Optional[str]) -> Optional[datetime]:
    """Workday returns strings like 'Posted Today', 'Posted Yesterday',
    'Posted 5 Days Ago', 'Posted 30+ Days Ago'."""
    if not s:
        return None
    sl = s.lower()
    today = datetime.now(timezone.utc)
    if "today" in sl:
        return today
    if "yesterday" in sl:
        return today - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s+day", sl)
    if m:
        return today - timedelta(days=int(m.group(1)))
    return None


def _parse_md_list_date(s: str) -> Optional[datetime]:
    """SimplifyJobs uses formats like 'Jan 06', 'Apr 28' (no year).
    Assumes current year; if that lands in the future, it's last year."""
    s = (s or "").strip()
    if not s:
        return None
    today = datetime.now(timezone.utc)
    for fmt in ("%b %d", "%B %d"):
        try:
            dt = datetime.strptime(s, fmt).replace(
                year=today.year, tzinfo=timezone.utc
            )
            if dt > today + timedelta(days=1):
                dt = dt.replace(year=today.year - 1)
            return dt
        except ValueError:
            continue
    return None


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
        date_str = cells_clean[4] if len(cells_clean) > 4 else ""
        posted_at = _parse_md_list_date(date_str)

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
            posted_at=posted_at,
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
        posted_at = _parse_iso(j.get("updated_at") or j.get("first_published"))
        out.append(Posting(
            company=slug.title(),
            role=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            source=f"Greenhouse:{slug}",
            posted_at=posted_at,
        ))
    return out


# ---------- Workday ----------
#
# Workday-hosted boards expose a JSON endpoint at:
#   POST https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
#
# To find a company's values: open their careers page, copy the URL. The first
# subdomain is the tenant (e.g. jpmc), the path segment after the locale is the
# site (e.g. ExternalCareerSite from .../en-US/ExternalCareerSite/).
#
# We paginate up to max_jobs per company per run.

# Targeted search terms — Workday's default sort is by recency, which buries
# entry-level / intern / 2026 program postings under noise (VP/Director roles).
# Running multiple searches and unioning the results gets us the early-career
# postings reliably. Dedupe is handled downstream by Posting.id.
WORKDAY_SEARCH_TERMS = ["2026", "intern", "summer", "analyst", "associate", "new grad", "campus"]


def fetch_workday(label: str, base: str, site: str, max_per_search: int = 100) -> list[Posting]:
    base = base.rstrip("/")
    try:
        tenant = base.split("//", 1)[1].split(".", 1)[0]
    except IndexError:
        print(f"  ! workday {label}: invalid base URL {base!r}")
        return []

    api_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    headers = {**UA, "Accept": "application/json", "Content-Type": "application/json"}
    out: list[Posting] = []
    seen_paths: set[str] = set()
    page_size = 20
    failed_immediately = False

    for term in WORKDAY_SEARCH_TERMS:
        if failed_immediately:
            break
        offset = 0
        while offset < max_per_search:
            body = {
                "appliedFacets": {},
                "limit": page_size,
                "offset": offset,
                "searchText": term,
            }
            try:
                r = requests.post(api_url, json=body, headers=headers, timeout=TIMEOUT)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                if offset == 0 and term == WORKDAY_SEARCH_TERMS[0]:
                    print(f"  ! workday {label}: {e}")
                    failed_immediately = True
                break
            jobs = data.get("jobPostings", [])
            if not jobs:
                break
            for j in jobs:
                external_path = j.get("externalPath", "") or ""
                if external_path in seen_paths:
                    continue
                seen_paths.add(external_path)
                # Workday's clickable URL is {base}/en-US/{site}{externalPath};
                # without the locale + site segment the page 404s.
                url = f"{base}/en-US/{site}{external_path}" if external_path else ""
                posted_at = _parse_workday_posted(j.get("postedOn"))
                out.append(Posting(
                    company=label,
                    role=j.get("title", ""),
                    location=j.get("locationsText", "") or "",
                    url=url,
                    source=f"Workday:{label}",
                    posted_at=posted_at,
                ))
            if len(jobs) < page_size:
                break
            offset += page_size
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
        posted_at = _parse_epoch_ms(j.get("createdAt"))
        out.append(Posting(
            company=slug.title(),
            role=j.get("text", ""),
            location=cats.get("location", ""),
            url=j.get("hostedUrl", ""),
            source=f"Lever:{slug}",
            posted_at=posted_at,
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
