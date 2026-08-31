"""Title regexes, age filter, and config sanity."""
from datetime import datetime, timedelta, timezone

import main
from scrapers import Posting


def mk(role="Software Engineer", location="Austin, TX", hours=None):
    at = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
        if hours is not None
        else None
    )
    return Posting(
        company="X", role=role, location=location, url="https://x",
        source="Greenhouse:x", posted_at=at,
    )


MIN_CFG = {
    "us_only": True,
    "max_age_days": 1,
    "reject_if_title_contains": ["summer 2025"],
    "role_categories": [{"key": "swe", "label": "swe", "keywords": ["software engineer"]}],
    "posting_types": [{"key": "new_grad", "label": "ng", "requires_any": ["new grad"]}],
}


class TestInternReject:
    def test_intern_variants(self):
        for role in (
            "SWE Intern", "Software Engineering Internship", "2026 Co-op Engineer",
            "Fall Coop Program", "Engineering Co Op",
        ):
            assert main._INTERN_TITLE_RE.search(role), role

    def test_not_intern(self):
        for role in (
            "International Solutions Engineer", "Internal Tools Engineer",
            "Software Engineer, New Grad",
        ):
            assert not main._INTERN_TITLE_RE.search(role), role


class TestSeniorTitle:
    def test_senior(self):
        for role in (
            "Senior Solutions Engineer", "Software Engineer III", "Staff Software Engineer",
            "Staff+ Software Engineer, RL Data Platform", "Team Lead, ARC Engineering",
            "Field Engineering Enablement Leader", "Engineering Manager, Platform",
            "Principal Engineer", "Lead Software Engineer", "VP of Engineering",
            "Director of Solutions Engineering", "TLM, Production Engineering",
            "Sr Mgr, Engineering", "Dir, Product", "SNR Engineer",
            "Group Product Manager, Verticals",
        ):
            assert main._looks_senior(role), role

    def test_not_senior(self):
        for role in (
            "Solutions Engineer", "Software Engineer II", "Associate Solutions Engineer",
            "Product Manager", "Associate Product Manager", "Product Manager, New Grad",
            "APM, Monetization", "Technology Consultant", "Product Analyst",
            "Solutions Engineer, Okta (North East)",
        ):
            assert not main._looks_senior(role), role


class TestAgeFilter:
    def test_boundaries(self):
        assert main.categorize(mk(hours=10), MIN_CFG, {}) is not None
        assert main.categorize(mk(hours=20), MIN_CFG, {}) is not None
        assert main.categorize(mk(hours=26), MIN_CFG, {}) is None
        assert main.categorize(mk(hours=70), MIN_CFG, {}) is None

    def test_undated_kept(self):
        assert main.categorize(mk(hours=None), MIN_CFG, {}) is not None

    def test_config_sanity(self):
        cfg = main.load_config()
        assert cfg["max_age_days"] == 1
        assert isinstance(cfg["llm"]["model"], list) and cfg["llm"]["model"]
        assert cfg["locations"]["hotspots"]
        for cat in cfg["role_categories"]:
            assert cat["keywords"]
        for pt in cfg["posting_types"]:
            if pt["key"] == "verified_entry":
                assert "requires_any" not in pt
