"""Fetch postings from community GitHub lists, Greenhouse, Lever, and Workday."""
from __future__ import annotations

import hashlib
import html as _html
import os
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
    api_id: Optional[str] = None       # source-specific id/path for detail lookups
    description: Optional[str] = None  # embedded description (Ashby/Lever)
    verdict: Optional[dict] = None     # set when promoted by the LLM verifier
    hotspot: bool = False              # in one of the configured hot-spot areas
    fit: Optional[int] = None         # LLM fit score 1-5 against the profile

    @property
    def id(self) -> str:
        key = f"{self.company.lower().strip()}|{self.role.lower().strip()}|{self.url.strip()}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = {**asdict(self), "id": self.id}
        if self.posted_at:
            d["posted_at"] = self.posted_at.isoformat()
        return d


# ---------- HTML -> text (for LLM consumption of job descriptions) ----------

_BLOCK_TAG_RE = re.compile(
    r"(?i)</?(?:p|div|br|li|tr|h[1-6]|ul|ol|table|section|article)[^>]*>"
)
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def html_to_text(html: str) -> str:
    """Crude HTML → text conversion; only used to feed job descriptions
    to the LLM verifier, so perfect formatting doesn't matter."""
    if not html:
        return ""
    s = _BLOCK_TAG_RE.sub("\n", html)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


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
        # first_published is the real posting time; updated_at bumps on any
        # edit and would let old-but-touched postings through as fresh.
        posted_at = _parse_iso(j.get("first_published") or j.get("updated_at"))
        out.append(Posting(
            company=slug.title(),
            role=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            source=f"Greenhouse:{slug}",
            posted_at=posted_at,
            api_id=str(j.get("id") or ""),
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
WORKDAY_SEARCH_TERMS = [
    "2026", "intern", "summer", "analyst", "associate", "new grad", "campus",
    "consultant", "engineer", "developer", "product",
]


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
                    api_id=external_path,
                ))
            if len(jobs) < page_size:
                break
            offset += page_size
    return out


# ---------- Lever ----------

def fetch_lever(slug: str) -> list[Posting]:
    # NOTE: Lever changed their public API — the old `?mode=json` suffix now
    # 404s. The plain endpoint returns the same JSON array, and it includes
    # the full description, so no per-job fetch is needed for verification.
    url = f"https://api.lever.co/v0/postings/{slug}"
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
        desc = (
            j.get("descriptionPlain")
            or j.get("descriptionBodyPlain")
            or html_to_text(j.get("description") or "")
        )
        out.append(Posting(
            company=slug.title(),
            role=j.get("text", ""),
            location=cats.get("location", ""),
            url=j.get("hostedUrl", ""),
            source=f"Lever:{slug}",
            posted_at=posted_at,
            description=(desc or "")[:8000] or None,
        ))
    return out


# ---------- Ashby ----------
#
# Ashby-hosted boards expose a public JSON API at:
#   https://api.ashbyhq.com/posting-api/job-board/{org}
# The response embeds the full description, so postings from this source go
# straight to the LLM verifier with no extra fetching.

def fetch_ashby(slug: str) -> list[Posting]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
    except Exception as e:
        print(f"  ! ashby {slug}: {e}")
        return []
    out = []
    for j in jobs:
        if j.get("isListed") is False:
            continue
        title = (j.get("title") or "").strip()
        if not title:
            continue
        desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")
        location = (j.get("location") or "").strip()
        if not location and j.get("isRemote"):
            location = "Remote"
        out.append(Posting(
            company=slug.title(),
            role=title,
            location=location,
            url=j.get("jobUrl", "") or "",
            source=f"Ashby:{slug}",
            posted_at=_parse_iso(j.get("publishedAt")),
            api_id=j.get("id") or "",
            description=(desc or "")[:8000] or None,
        ))
    return out


# ---------- Remote job boards ----------
#
# RemoteOK exposes a public JSON API (https://remoteok.com/api). Their terms
# ask for a descriptive User-Agent and attribution — both in UA above and in
# the README. The first element of the response is a legal notice, not a job.
#
# WeWorkRemotely publishes category RSS feeds; items carry a <region>
# element (e.g. "Anywhere", "USA Only") that we use as the location.

def fetch_remoteok() -> list[Posting]:
    try:
        r = requests.get("https://remoteok.com/api", headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        jobs = [j for j in r.json() if isinstance(j, dict) and j.get("position")]
    except Exception as e:
        print(f"  ! remoteok: {e}")
        return []
    out = []
    for j in jobs:
        company = str(j.get("company") or "").strip()
        role = str(j.get("position") or "").strip()
        if not company or not role:
            continue
        desc = html_to_text(j.get("description") or "")
        out.append(Posting(
            company=company,
            role=role,
            location=str(j.get("location") or "Remote").strip() or "Remote",
            url=j.get("url") or j.get("apply_url") or "",
            source="RemoteOK",
            posted_at=_parse_iso(j.get("date")),
            description=(desc or "")[:8000] or None,
        ))
    return out


def _parse_rfc822(s: str) -> Optional[datetime]:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None


def fetch_wwr(category: str) -> list[Posting]:
    import xml.etree.ElementTree as ET

    url = f"https://weworkremotely.com/categories/{category}.rss"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"  ! wwr {category}: {e}")
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if ":" in title:
            company, role = (part.strip() for part in title.split(":", 1))
        else:
            company, role = title, title
        if not company or not role:
            continue
        region = (item.findtext("region") or "").strip() or "Anywhere"
        if region.lower() in ("anywhere", "worldwide", "usa only", "north america"):
            location = f"Remote — {region}"
        else:
            location = region  # e.g. "Asia Only" -> dropped by the US filter
        desc = html_to_text(item.findtext("description") or "")
        out.append(Posting(
            company=company,
            role=role,
            location=location,
            url=(item.findtext("link") or "").strip(),
            source=f"WWR:{category}",
            posted_at=_parse_rfc822(item.findtext("pubDate") or ""),
            description=(desc or "")[:8000] or None,
        ))
    return out


# ---------- JSearch (RapidAPI) — LinkedIn / Indeed / Glassdoor ----------
#
# LinkedIn and Indeed can't be scraped directly (auth walls, ToS). JSearch
# serves the same postings as structured JSON, descriptions included, so
# the verifier reads them with no extra fetching. The key lives in the
# JSEARCH_API_KEY secret; main.py rotates one query per run to respect
# free-tier quotas.

JSEARCH_HOST = "jsearch.p.rapidapi.com"


def _parse_jsearch_datetime(s: Optional[str]) -> Optional[datetime]:
    """JSearch returns '2026-08-31 13:22:01' (already UTC, no suffix)."""
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def fetch_jsearch(query: str, cfg: dict) -> list[Posting]:
    api_key = os.environ.get("JSEARCH_API_KEY", "")
    if not api_key:
        return []
    try:
        r = requests.get(
            f"https://{JSEARCH_HOST}/search",
            params={
                "query": query,
                "date_posted": cfg.get("date_posted", "3days"),
                "employment_types": cfg.get("employment_types", "FULLTIME"),
                "num_pages": "1",
            },
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": JSEARCH_HOST,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        body = r.json() or {}
        data = body.get("data") or []
        if not data:
            print(
                f"    ! jsearch returned no data: status={body.get('status')!r} "
                f"message={str(body.get('message'))[:160]!r} keys={sorted(body)[:6]}"
            )
    except Exception as e:
        print(f"  ! jsearch: {e}")
        return []
    out = []
    for j in data:
        company = str(j.get("employer_name") or "").strip()
        role = str(j.get("job_title") or "").strip()
        if not company or not role:
            continue
        country = str(j.get("job_country") or "").strip()
        if j.get("job_is_remote"):
            location = "Remote — US" if country in ("US", "United States", "") else f"Remote — {country}"
        else:
            location = ", ".join(filter(None, [j.get("job_city"), j.get("job_state")])) or country
        desc = html_to_text(j.get("job_description") or "")
        out.append(Posting(
            company=company,
            role=role,
            location=location,
            url=j.get("job_apply_link") or "",
            source="JSearch",
            posted_at=_parse_jsearch_datetime(j.get("job_posted_at_datetime_utc")),
            description=(desc or "")[:8000] or None,
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
