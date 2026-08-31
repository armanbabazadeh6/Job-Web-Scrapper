"""Entry point. Loads config, runs all scrapers, categorizes, diffs, verifies, and notifies."""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

import llm_verify
from notify import notify_discord_postings
from scrapers import (
    Posting,
    fetch_ashby,
    fetch_github_lists,
    fetch_greenhouse,
    fetch_lever,
    fetch_remoteok,
    fetch_watch_url,
    fetch_workday,
    fetch_wwr,
)

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
SEEN_PATH = ROOT / "seen.json"
VERDICTS_PATH = ROOT / "verdicts.json"


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_seen(seen: dict) -> None:
    SEEN_PATH.write_text(
        json.dumps(_prune_seen(seen), indent=2, sort_keys=True), encoding="utf-8"
    )


def _prune_seen(seen: dict) -> dict:
    """Drop entries that can no longer re-notify: dated postings older than 30
    days (the age filter already blocks their return) and undated
    community-list entries older than 90 days. Keeps seen.json bounded."""
    today = datetime.now(timezone.utc).date()
    keep: dict = {}
    for k, v in seen.items():
        if not isinstance(v, dict):
            continue
        try:
            age = (
                today - datetime.strptime(v.get("first_seen", ""), "%Y-%m-%d").date()
            ).days
        except ValueError:
            keep[k] = v
            continue
        if age <= (30 if v.get("posted_at") else 90):
            keep[k] = v
    return keep


SENIOR_MARKERS = [
    "senior", "vice president", " vp,", " vp ", "director", "managing",
    "principal", "head of", "chief", "executive", "lead ", "staff ",
]

# This pipeline is FULL-TIME new-grad roles only. Word-boundary regex so
# "International Solutions Engineer" / "internal tools" don't false-positive.
_INTERN_TITLE_RE = re.compile(r"\bintern(ship)?s?\b|\bco[\s-]?ops?\b", re.IGNORECASE)

# Titles that are obviously not entry-level. These never reach the LLM — no
# point paying to classify what the title already says. Word-boundary regex
# so "Staff+" / "Lead," / "Leader" all match, while words like "diversity"
# (contains "iii" as substring) don't.
#
# "manager" is exempt when preceded by "product " — Product Manager is the
# one discipline where "manager" is the standard ENTRY-level title (APM
# programs, "Product Manager, New Grad"). Plain "Engineering Manager",
# "Manager, X" etc. still match.
_SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr|snr|staff|principal|leader|lead|leadership|architect|"
    r"distinguished|expert|(?<!product )manager|mgr|managing|director|dir|"
    r"tlm|vice president|vp|head of|chief|group product manager)\b"
    r"|\b(iii|iv)\b",
    re.IGNORECASE,
)


def _looks_senior(role: str) -> bool:
    return bool(_SENIOR_TITLE_RE.search(role))


# Country / city tokens that unambiguously indicate non-US locations.
# Curated to avoid US-city overlaps (no "Paris", "Madrid", "Manchester" etc.,
# since those are also US cities). Country tokens are safer than city tokens
# because Workday locations almost always include the country at the end.
NON_US_LOCATION_TOKENS = [
    # Countries
    "india", "united kingdom", "canada", "germany", "france", "spain", "italy",
    "netherlands", "belgium", "switzerland", "austria", "sweden", "norway",
    "denmark", "finland", "ireland", "poland", "czech", "greece", "portugal",
    "romania", "hungary", "russia", "ukraine", "turkey", "israel", "saudi",
    "uae", "egypt", "south africa", "nigeria", "kenya", "brazil", "argentina",
    "chile", "colombia", "peru", "australia", "new zealand", "japan",
    "china", "singapore", "hong kong", "vietnam", "thailand", "philippines",
    "indonesia", "malaysia", "south korea", "taiwan", "cyprus", "luxembourg",
    "iceland", "scotland", "wales",
    "serbia", "bulgaria", "croatia", "slovenia", "slovakia", "estonia",
    "latvia", "lithuania", "bosnia", "macedonia", "albania", "moldova",
    "belarus",
    # Mexico is tricky (city of New Mexico contains "mexico"), so check more carefully:
    " mexico ", ", mexico", "mexico city",
    # Major non-US cities (low US-overlap risk)
    "bangalore", "bengaluru", "hyderabad", "mumbai", "gurugram", "gurgaon",
    "noida", "ahmedabad", "chennai", "kochi", "pune", "kolkata", "jaipur",
    "manila", "jakarta", "bangkok", "hanoi", "ho chi minh", "kuala lumpur",
    "seoul", "taipei", "sydney", "melbourne", "brisbane",
    "toronto", "vancouver", "montreal", "ottawa", "calgary", "edmonton",
    "shanghai", "beijing", "shenzhen", "guangzhou", "chengdu",
    "tokyo", "kyoto", "osaka", "yokohama",
    "milano", "milan", "warsaw", "krakow", "prague",
    "zurich", "stockholm", "tel aviv", "istanbul", "moscow",
    "amsterdam", "rotterdam", "the hague", "belgrade", "sofia", "zagreb",
    "ljubljana", "bratislava", "tallinn", "riga", "vilnius", "sarajevo",
    "skopje", "tirana",
    "buenos aires", "sao paulo", "rio de janeiro", "santiago",
    "auckland", "wellington", "nicosia", "limassol",
    "cairo", "lagos", "nairobi", "johannesburg", "cape town",
    "abu dhabi", "doha", "dubai", "riyadh",
]


# Positive US indicators (no state abbreviations — those go through regex below
# to avoid matches like ", IN" eating "Bengaluru, India").
US_LOCATION_TOKENS = [
    "united states", "usa", "u.s.a", " u.s.", "u.s.,", "u.s. ",
    # Full state names. "Georgia" and "Washington" intentionally omitted —
    # they overlap with Georgia (country) and Washington (DC vs. state).
    # State abbreviation regex below catches ", GA" / ", WA" cases.
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "west virginia", "wisconsin", "wyoming",
    # Common US tech hubs / cities (low international overlap)
    "san francisco", "los angeles", "san diego", "san jose", "houston",
    "phoenix", "philadelphia", "dallas", "austin", "seattle", "denver",
    "atlanta", "minneapolis", "portland", "raleigh", "boston", "san antonio",
    "indianapolis", "milwaukee", "albuquerque", "tucson", "sacramento",
    "kansas city", "palo alto", "mountain view", "cupertino", "sunnyvale",
    "menlo park", "redwood city", "san mateo", "santa clara", "redmond",
    "bellevue", "san francisco bay area", "bay area",
]

# State abbreviations matched with word boundaries to avoid false positives like
# ", IN" matching "India" or ", CA" matching "Canada". Pattern: comma + optional
# whitespace + 2-letter abbrev + word boundary (end of word, not letter).
_US_STATE_ABBR_RE = re.compile(
    r",\s*(?:al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|"
    r"mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|"
    r"vt|va|wa|wv|wi|wy|dc)\b",
    re.IGNORECASE,
)


def is_non_us_location(location: str) -> bool:
    """Returns True if the location should be filtered out as non-US.

    Strict mode:
      - empty/unknown → keep (return False)
      - has US state abbrev (e.g. ", TX") → keep
      - has US indicator (full state name, "United States", US city) → keep
      - has non-US indicator → reject
      - remote with no clear country ("Remote", "Remote — US") → keep
      - anything else ambiguous → reject"""
    if not location:
        return False
    loc = location.lower()
    if _US_STATE_ABBR_RE.search(loc):
        return False
    if any(tok in loc for tok in US_LOCATION_TOKENS):
        return False
    if any(tok in loc for tok in NON_US_LOCATION_TOKENS):
        return True
    if "remote" in loc or "worldwide" in loc or "anywhere" in loc:
        return False  # remote without a stated country — assume US-eligible
    return True


def _fetch_boards(fn, args_list: list, workers: int = 8) -> list[Posting]:
    """Fetch board-type sources in parallel. Every fetcher swallows its own
    errors and returns [], so one dead board costs nothing but its slot."""
    from concurrent.futures import ThreadPoolExecutor

    out: list[Posting] = []
    if not args_list:
        return out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for postings in ex.map(fn, args_list):
            out.extend(postings)
    return out


def _location_tier(location: str, loc_cfg: dict) -> str:
    """hotspot, remote, or other."""
    loc = (location or "").lower()
    if not loc:
        return "other"
    hotspots = [str(h).lower() for h in loc_cfg.get("hotspots") or []]
    if any(h in loc for h in hotspots):
        return "hotspot"
    if "remote" in loc:
        return "remote"
    return "other"


def categorize(
    p: Posting, cfg: dict, source_overrides: dict
) -> Optional[tuple[str, str]]:
    """Returns (posting_type_key, role_category_key) or None if filtered out.

    posting_type_key may be the special value "verify": role keywords
    matched but the title gives no seniority signal, so the posting goes
    through the LLM entry-level verifier (see llm_verify.py).
    """
    title_l = p.role.lower()

    if cfg.get("us_only", True) and is_non_us_location(p.location):
        return None

    # Hard location filter, if enabled (mode: only keeps hot spots + remote).
    loc_cfg = cfg.get("locations") or {}
    if (loc_cfg.get("mode") or "prefer") == "only":
        if _location_tier(p.location, loc_cfg) == "other":
            return None

    # Age filter: drop postings older than max_age_days when posted_at is known.
    # Postings without a parseable date are kept (most GitHub list entries).
    max_age = cfg.get("max_age_days", 0)
    if max_age and p.posted_at is not None:
        posted = p.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - posted).total_seconds() / 86400
        if age_days > max_age:
            return None

    for bad in cfg.get("reject_if_title_contains", []):
        if bad.lower() in title_l:
            return None

    # Full-time new-grad pipeline only — no internships or co-ops.
    if _INTERN_TITLE_RE.search(p.role):
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
        if any(req.lower() in title_l for req in pt.get("requires_any") or []):
            posting_type = pt["key"]
            break

    if posting_type is None and override.get("finance_titles"):
        if any(s in title_l for s in SENIOR_MARKERS):
            return None  # too senior for early-career bucket
        if "analyst" in title_l or "associate" in title_l:
            posting_type = "new_grad"

    if posting_type is None:
        # No explicit new-grad signal in the title. If the title looks
        # senior it's dropped for free; otherwise the LLM reads the
        # description and decides.
        if _looks_senior(p.role):
            return None
        return ("verify", role_cat)

    return (posting_type, role_cat)


def _verify_note(v: dict) -> str:
    """Compact one-line summary of a verdict (used in logs, not Discord)."""
    parts: list[str] = []
    if v.get("verified"):
        yrs = v.get("years")
        parts.append("verified" if yrs is None else f"verified {yrs}y")
    else:
        parts.append("unverified")
    if v.get("salary"):
        parts.append(str(v["salary"]))
    fit = v.get("fit_score")
    if fit:
        parts.append(f"fit {fit}/5")
    if v.get("clearance_required"):
        parts.append("clearance")
    return " · ".join(parts)


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
    all_postings.extend(_fetch_boards(fetch_greenhouse, cfg.get("greenhouse_boards", [])))

    print("-> Lever boards")
    all_postings.extend(_fetch_boards(fetch_lever, cfg.get("lever_boards", [])))

    print("-> Ashby boards")
    all_postings.extend(_fetch_boards(fetch_ashby, cfg.get("ashby_boards", [])))

    print("-> Workday boards")
    all_postings.extend(_fetch_boards(
        lambda wd: fetch_workday(wd["name"], wd["base"], wd["site"]),
        cfg.get("workday_boards") or [],
    ))

    print("-> Remote boards")
    rb = cfg.get("remote_boards") or {}
    if rb.get("remoteok"):
        all_postings.extend(fetch_remoteok())
    all_postings.extend(_fetch_boards(fetch_wwr, rb.get("weworkremotely") or []))

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

    today = datetime.now(timezone.utc).date().isoformat()
    verdict_results: dict[str, dict] = {}

    def _seen_entry(p: Posting, key: tuple[str, str]) -> dict:
        d = {
            "first_seen": today,
            "company": p.company,
            "role": p.role,
            "url": p.url,
            "location": p.location,
            "source": p.source,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "posting_type": key[0],
            "role_category": key[1],
        }
        v = verdict_results.get(p.id)
        if v:
            d["verified"] = bool(v.get("verified"))
            if v.get("years") is not None:
                d["years_required"] = v["years"]
            if v.get("salary"):
                d["salary"] = v["salary"]
            if v.get("fit_score") is not None:
                d["fit_score"] = v["fit_score"]
            if v.get("clearance_required"):
                d["clearance_required"] = True
            if v.get("reason"):
                d["verify_reason"] = v["reason"]
        return d

    # First run: silently populate seen.json, no notifications. Postings that
    # need verification stay unseen so they flow through the verifier next
    # run instead of being buried.
    if first_run:
        print("   first run — populating seen.json silently, no notification sent")
        for key, postings in groups.items():
            if key[0] == "verify":
                continue
            for p in postings:
                seen[p.id] = _seen_entry(p, key)
        save_seen(seen)
        return 0

    # Diff against seen. Cross-source dedup: the same company+role arriving
    # via a community list AND a company board is one job — ping it once.
    dedup_days = int(cfg.get("dedup_days", 3))
    recent_pairs: set[tuple[str, str]] = set()
    if dedup_days:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=dedup_days)
        ).date().isoformat()
        for v in seen.values():
            if isinstance(v, dict) and (v.get("first_seen") or "9999") >= cutoff:
                recent_pairs.add(
                    (str(v.get("company", "")).lower(), str(v.get("role", "")).lower())
                )

    new_groups: dict[tuple[str, str], list[Posting]] = defaultdict(list)
    hot_tokens = [
        str(h).lower()
        for h in ((cfg.get("locations") or {}).get("hotspots") or [])
    ]
    for key, postings in groups.items():
        for p in postings:
            if p.id in seen:
                continue
            if dedup_days:
                pair = (p.company.lower(), p.role.lower())
                if pair in recent_pairs:
                    continue
                recent_pairs.add(pair)
            if hot_tokens and p.location and any(
                t in p.location.lower() for t in hot_tokens
            ):
                p = replace(p, hotspot=True)
            new_groups[key].append(p)

    # ---- LLM entry-level verification for ambiguous titles ----
    verify_by_cat: dict[str, list[Posting]] = defaultdict(list)
    for key in list(new_groups.keys()):
        if key[0] == "verify":
            for p in new_groups.pop(key):
                verify_by_cat[key[1]].append(p)

    verdict_results: dict[str, dict] = {}
    verify_descs: dict[str, Optional[str]] = {}
    workday_lookup = {
        f"Workday:{wd['name']}": (wd["base"], wd["site"])
        for wd in cfg.get("workday_boards") or []
    }
    if verify_by_cat:
        items = [p for ps in verify_by_cat.values() for p in ps]
        llm_cfg = {**(cfg.get("llm") or {}), "profile": cfg.get("profile") or {}}
        print(f"-> Verifying {len(items)} ambiguous posting(s) for entry-level")
        verdict_results, verdicts, verify_descs = llm_verify.verify_postings(
            items, workday_lookup, llm_cfg, llm_verify.load_verdicts(VERDICTS_PATH)
        )
        llm_verify.save_verdicts(VERDICTS_PATH, verdicts)

        filter_clearance = bool(cfg.get("filter_clearance"))
        passed = 0
        for cat, ps in verify_by_cat.items():
            for p in ps:
                v = verdict_results.get(p.id) or {}
                if not v.get("entry_level") or v.get("seniority") == "intern":
                    continue
                if filter_clearance and v.get("clearance_required"):
                    continue
                passed += 1
                new_groups[("verified_entry", cat)].append(
                    replace(
                        p,
                        verdict=v,
                        fit=v.get("fit_score"),
                        description=verify_descs.get(p.id) or p.description,
                    )
                )
        print(f"   {passed}/{len(items)} passed entry-level verification")

    # Explicit new-grad postings skip the verifier, so their embeds would
    # have no JD excerpt. Fetch descriptions for them cheaply (one call per
    # new posting, capped so bursts can't stall the run).
    desc_budget = 30
    for key, postings in new_groups.items():
        if key[0] == "verify":
            continue
        for i, p in enumerate(postings):
            if p.description or desc_budget <= 0:
                continue
            d = llm_verify.fetch_description(p, workday_lookup)
            desc_budget -= 1
            if d:
                postings[i] = replace(p, description=d[:4000])

    new_total = sum(len(v) for v in new_groups.values())
    print(f"   {new_total} are new (not in seen.json)")

    if new_total > 0:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        type_meta = {pt["key"]: pt for pt in cfg["posting_types"]}
        cat_meta = {c["key"]: c for c in cfg["role_categories"]}
        type_order = [pt["key"] for pt in cfg["posting_types"]]
        cat_order = [c["key"] for c in cfg["role_categories"]]
        notify_discord_postings(
            new_groups, type_meta, cat_meta, type_order, cat_order, webhook,
            favorites=cfg.get("favorite_companies") or [],
        )

        for key, postings in new_groups.items():
            for p in postings:
                seen[p.id] = _seen_entry(p, key)
        save_seen(seen)
        print(f"   notified + saved {new_total} new postings")
    else:
        print("   nothing new")

    return 0


if __name__ == "__main__":
    sys.exit(main())
