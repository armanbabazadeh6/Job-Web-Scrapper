"""Entry-level verification for ambiguous job titles.

Postings reach this module only after the cheap keyword filters in main.py
matched a role category but the title itself gives no seniority signal
(e.g. "Solutions Engineer", "Software Engineer I"). This module:

  1. Fetches the full job description (per-job APIs for Greenhouse/Workday,
     embedded descriptions for Ashby/Lever, URL-pattern fallback for
     community-list links).
  2. Applies a conservative regex screen that hard-rejects descriptions
     demanding lots of experience — free, no API calls.
  3. Sends the rest to an LLM through any OpenAI-compatible
     chat/completions endpoint (defaults to Google Gemini's free tier).

Cost control: verdicts are cached in verdicts.json by posting id (nothing is
ever classified twice), postings are batched per LLM call, and each run is
capped. If the API key is missing or the API fails, everything degrades to
the regex screen so Discord notifications never stop.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from scrapers import Posting, html_to_text

TIMEOUT = 45
VERDICT_KEEP_DAYS = 90

# ---------- regex screen ----------

_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE)
_RANGE_PREFIX_RE = re.compile(r"(\d{1,2})\s*(?:-|–|—|to)\s*$")


def extract_required_years(text: Optional[str]) -> Optional[int]:
    """Minimum explicitly-stated experience requirement, or None if none found.

    Only counts mentions within ~40 chars of the word "experience" so company
    blurbs like "serving customers for 20 years" don't count. For ranges like
    "3-5 years", the minimum (3) is used. Deliberately conservative — the LLM
    is the real judge; this screen exists to hard-reject blatant cases for
    free and to keep working when the LLM is unavailable.
    """
    if not text:
        return None
    reqs: list[int] = []
    for m in _YEARS_RE.finditer(text):
        window = text[m.end():m.end() + 40].lower()
        before = text[max(0, m.start() - 40):m.start()].lower()
        if "experience" not in window and "experience" not in before:
            continue
        years = int(m.group(1))
        rng = _RANGE_PREFIX_RE.search(before)
        if rng:
            years = int(rng.group(1))
        reqs.append(years)
    return min(reqs) if reqs else None


def regex_screen(text: Optional[str], hard_reject_years: int) -> Optional[bool]:
    """True/False verdict from the regex alone, or None if no signal."""
    yrs = extract_required_years(text)
    if yrs is None:
        return None
    return yrs < hard_reject_years


# ---------- description fetching ----------

def _greenhouse_description(slug: str, job_id: str) -> Optional[str]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return html_to_text(r.json().get("content") or "") or None


def _workday_description(base: str, site: str, external_path: str) -> Optional[str]:
    tenant = base.split("//", 1)[1].split(".", 1)[0]
    url = f"{base.rstrip('/')}/wday/cxs/{tenant}/{site}{external_path}"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    return (r.json().get("jobPostingInfo") or {}).get("jobDescription") or None


# Community-list links often point straight at ATS job pages — pull the
# description from the matching public API when the URL pattern is known.
_GH_URL_RE = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board/)?"
    r"([A-Za-z0-9_.-]+)/jobs/(\d+)"
)
_WD_URL_RE = re.compile(
    r"(https://[A-Za-z0-9]+\.wd\d+\.myworkdayjobs\.com)/"
    r"[a-zA-Z]{2}(?:-[a-zA-Z]{2})?/([A-Za-z0-9_-]+)(/job/[^?#]+)"
)
_LEVER_URL_RE = re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)/([A-Za-z0-9-]+)")
_ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9-]+)")

# Ashby has no per-job endpoint, so community-list links to Ashby pages are
# resolved by pulling the org's board once per process and matching by id.
_ASHBY_BOARD_CACHE: dict[str, dict[str, str]] = {}


def _lever_description(slug: str, job_id: str) -> Optional[str]:
    r = requests.get(
        f"https://api.lever.co/v0/postings/{slug}/{job_id}", timeout=TIMEOUT
    )
    r.raise_for_status()
    j = r.json()
    desc = (
        j.get("descriptionPlain")
        or j.get("descriptionBodyPlain")
        or html_to_text(j.get("description") or "")
    )
    return (desc or "").strip() or None


def _ashby_description(org: str, job_id: str) -> Optional[str]:
    board = _ASHBY_BOARD_CACHE.get(org)
    if board is None:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{org}", timeout=TIMEOUT
        )
        r.raise_for_status()
        board = {
            str(j.get("id")): (
                j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")
            )
            for j in r.json().get("jobs", [])
        }
        _ASHBY_BOARD_CACHE[org] = board
    return board.get(job_id) or None


def fetch_description(p: Posting, workday_lookup: dict) -> Optional[str]:
    """Full description for a posting, or None when it can't be fetched."""
    if p.description:
        return p.description
    src = p.source or ""
    try:
        if src.startswith("Greenhouse:") and p.api_id:
            return _greenhouse_description(src.split(":", 1)[1], p.api_id)
        if src.startswith("Workday:") and p.api_id:
            base, site = workday_lookup[src]
            return _workday_description(base, site, p.api_id)
    except Exception as e:
        print(f"    ! desc fetch failed [{p.company} — {p.role}]: {e}")
        return None
    try:
        m = _GH_URL_RE.search(p.url or "")
        if m:
            return _greenhouse_description(m.group(1), m.group(2))
        m = _WD_URL_RE.search(p.url or "")
        if m:
            return _workday_description(m.group(1), m.group(2), m.group(3))
        m = _LEVER_URL_RE.search(p.url or "")
        if m:
            return _lever_description(m.group(1), m.group(2))
        m = _ASHBY_URL_RE.search(p.url or "")
        if m:
            return _ashby_description(m.group(1), m.group(2))
    except Exception as e:
        print(f"    ! desc fetch failed [{p.company} — {p.role}]: {e}")
    return None


# ---------- LLM ----------

SYSTEM_PROMPT = """You screen job postings for a new college graduate hunting their first FULL-TIME role. Internships and co-ops are NOT wanted.

You will receive a JSON array of jobs, each: {{"id": string, "title": string, "company": string, "description": string}}.

For each job, judge from the description text:
- years_required: minimum years of professional full-time experience the posting demands (0 if none stated, or if it targets students / recent graduates).
- entry_level: judged from the STATED experience requirement in the description, not the title's level connotation. years_required at or below {max_years} → entry_level true — even for titles like "Engineer II" or postings that call themselves "mid-level"; the stated requirement wins over the title. More than {max_years} years required, or explicitly senior scope in the description (leads a team, owns an org, staff-level breadth) → false. Internships and co-ops → false with seniority "intern". Full-time only.
- seniority: one of "intern", "new_grad", "junior", "mid", "senior", "lead", "staff", "principal", "manager", "director", "vp".
- salary: the salary or compensation range stated in the description (e.g. "$120k-$150k"), or null if none is stated.
- clearance_required: true if the posting requires an active security clearance or clearance eligibility (e.g. "active TS/SCI", "must be able to obtain a clearance") as a hard requirement; false if unmentioned or merely a plus.
- fit_score: 1-5 fit with the candidate profile below — 5 = strong match on timing, skills, and preferences; 1 = clear mismatch (wrong start timing for their availability, mismatched requirements). Score strictly from what the profile and description say.
- reason: at most 12 words, citing evidence from the description.

Candidate profile: {profile_json}

Respond with ONLY a JSON array (no markdown fences, no commentary), one object per input job, using the exact input ids:
[{{"id": "...", "entry_level": true, "years_required": 0, "seniority": "new_grad", "salary": "$120k-$150k", "clearance_required": false, "fit_score": 4, "reason": "..."}}]"""


def _parse_verdicts(content: str) -> list[dict]:
    s = (content or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON array in LLM response")
    arr = json.loads(s[start:end + 1])
    if not isinstance(arr, list):
        raise ValueError("LLM response is not a JSON array")
    return arr


def _call_llm(
    base_url: str, model: str, api_key: str, items: list[dict],
    max_years: int, profile_json: str,
) -> list[dict]:
    r = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(
                        max_years=max_years, profile_json=profile_json
                    ),
                },
                {"role": "user", "content": json.dumps(items)},
            ],
        },
        timeout=120,
    )
    if r.status_code >= 400:
        detail = " ".join((r.text or "").split())[:200]
        raise RuntimeError(f"HTTP {r.status_code} [{model}]: {detail}")
    content = ((r.json().get("choices") or [{}])[0].get("message") or {}).get(
        "content", ""
    )
    return _parse_verdicts(content)


def _list_models(base_url: str, api_key: str) -> list[str]:
    """Best-effort list of model ids the endpoint offers, for diagnostics."""
    try:
        r = requests.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if r.ok:
            return [m.get("id", "?") for m in (r.json().get("data") or [])]
    except Exception:
        pass
    return []


def _call_with_fallback(
    base_url: str,
    models: list[str],
    api_key: str,
    items: list[dict],
    max_years: int,
    state: dict,
    profile_json: str = "{}",
) -> Optional[list[dict]]:
    """Tries the last-known-good model first, then the configured chain.
    Each candidate gets two attempts. On total failure, logs the endpoint's
    model list once so a config fix needs zero guesswork."""
    if state.get("working"):
        candidates = [state["working"]] + [
            m for m in models if m != state["working"]
        ]
    else:
        candidates = list(models)
    for m in candidates:
        for attempt in (1, 2, 3):
            try:
                arr = _call_llm(
                    base_url, m, api_key, items, max_years, profile_json
                )
                if state.get("working") != m:
                    print(f"    LLM model in use: {m}")
                state["working"] = m
                return arr
            except Exception as e:
                print(f"    ! LLM {m} attempt {attempt} failed: {e}")
                # 503 "high demand" clears with patience; back off longer
                # on later attempts.
                time.sleep(5 if attempt == 1 else 15)
        time.sleep(5)
    if not state.get("probed"):
        state["probed"] = True
        avail = _list_models(base_url, api_key)
        if avail:
            print(f"    ! endpoint offers these models: {', '.join(avail)}")
    return None


def _coerce_years(y) -> Optional[int]:
    try:
        return int(float(y))
    except (TypeError, ValueError):
        return None


def _coerce_fit(y) -> Optional[int]:
    try:
        return min(5, max(1, int(float(y))))
    except (TypeError, ValueError):
        return None


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return bool(v)


def _verdict(
    entry_level: bool, years, verified: bool, reason: str, seniority=None,
    salary=None, clearance_required=False, fit_score=None,
) -> dict:
    return {
        "entry_level": bool(entry_level),
        "years": years,
        "verified": verified,
        "seniority": seniority,
        "salary": (str(salary)[:40] if salary else None),
        "clearance_required": bool(clearance_required),
        "fit_score": fit_score,
        "reason": (reason or "")[:120],
        "cached_at": datetime.now(timezone.utc).date().isoformat(),
    }


def verify_postings(
    postings: list[Posting],
    workday_lookup: dict,
    cfg: dict,
    verdicts: dict,
) -> tuple[dict[str, dict], dict, dict[str, Optional[str]]]:
    """Verifies postings; returns (results for these ids, updated cache,
    descriptions fetched along the way).

    Result fields: entry_level, years (int|None), verified (True = LLM made
    the call, False = regex screen / fallback did), seniority, salary,
    clearance_required, fit_score, reason. Descriptions are returned so the
    notifier can include a JD excerpt in the ping.
    """
    results: dict[str, dict] = {}
    descs: dict[str, Optional[str]] = {}
    todo: list[Posting] = []
    for p in postings:
        v = verdicts.get(p.id)
        if isinstance(v, dict):
            results[p.id] = v
        else:
            todo.append(p)
    if not todo:
        return results, verdicts, descs

    hard_reject = int(cfg.get("hard_reject_years", 4))
    max_years = int(cfg.get("max_years", 2))
    lenient = (cfg.get("fallback") or "lenient") == "lenient"
    api_key = os.environ.get(cfg.get("api_key_env") or "LLM_API_KEY", "")
    can_llm = bool(cfg.get("enabled", True)) and bool(api_key)

    cap = int(cfg.get("max_per_run", 50))
    if len(todo) > cap:
        print(f"   capping verification to {cap} of {len(todo)} (rest retry next run)")
        todo = todo[:cap]

    print(f"   verifier mode: {'LLM' if can_llm else 'regex fallback (no API key)'}")

    needs_llm: list[tuple[Posting, str]] = []
    for p in todo:
        desc = fetch_description(p, workday_lookup)
        descs[p.id] = desc
        if not desc:
            v = _verdict(False, None, False, "no description available")
        else:
            screen = regex_screen(desc, hard_reject)
            if screen is False:
                v = _verdict(
                    False, extract_required_years(desc), False,
                    "high experience requirement",
                )
            elif can_llm:
                needs_llm.append((p, desc))
                continue
            else:
                v = _verdict(lenient, None, False, "unverified fallback")
        results[p.id] = v
        verdicts[p.id] = v

    if needs_llm:
        base_url = cfg.get(
            "base_url", "https://generativelanguage.googleapis.com/v1beta/openai"
        )
        models = cfg.get("model") or "gemini-flash-latest"
        if isinstance(models, str):
            models = [models]
        batch_size = max(1, int(cfg.get("batch_size", 6)))
        truncate = int(cfg.get("max_description_chars", 3500))
        pause = float(cfg.get("seconds_between_calls", 5))

        state: dict = {"working": None, "probed": False}
        profile_json = json.dumps(cfg.get("profile") or {}, separators=(",", ":"))

        for i in range(0, len(needs_llm), batch_size):
            batch = needs_llm[i:i + batch_size]
            items = [
                {
                    "id": p.id,
                    "title": p.role,
                    "company": p.company,
                    "description": (desc or "")[:truncate],
                }
                for p, desc in batch
            ]
            arr = _call_with_fallback(
                base_url, models, api_key, items, max_years, state, profile_json
            )
            by_id: dict[str, dict] = {}
            if arr:
                for v in arr:
                    if isinstance(v, dict) and v.get("id"):
                        by_id[str(v["id"])] = v
            for p, _desc in batch:
                raw = by_id.get(p.id)
                if raw is None:
                    v = _verdict(lenient, None, False, "LLM verdict missing (fallback)")
                else:
                    yrs = _coerce_years(raw.get("years_required"))
                    ok = _truthy(raw.get("entry_level")) and (
                        yrs is None or yrs <= max_years
                    )
                    v = _verdict(
                        ok, yrs, True, str(raw.get("reason") or ""),
                        raw.get("seniority"), raw.get("salary"),
                        _truthy(raw.get("clearance_required")),
                        _coerce_fit(raw.get("fit_score")),
                    )
                results[p.id] = v
                verdicts[p.id] = v
            if i + batch_size < len(needs_llm):
                time.sleep(pause)

    return results, verdicts, descs


# ---------- verdict cache ----------

def load_verdicts(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_verdicts(path: Path, verdicts: dict) -> None:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=VERDICT_KEEP_DAYS)
    ).date().isoformat()
    pruned = {
        k: v for k, v in verdicts.items()
        if not (isinstance(v, dict) and str(v.get("cached_at", "")) < cutoff)
    }
    path.write_text(
        json.dumps(pruned, indent=2, sort_keys=True), encoding="utf-8"
    )
