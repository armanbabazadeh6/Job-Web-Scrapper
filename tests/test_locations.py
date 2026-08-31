"""Location tiers, hot spots, and the remote-US survival fix."""
from dataclasses import replace
from datetime import datetime, timezone

import main
import notify
from scrapers import Posting


CFG = main.load_config()
LOC = CFG["locations"]


def mk(role="Solutions Engineer", location="Austin, TX"):
    return Posting(company="X", role=role, location=location, url="https://x", source="Ashby:x")


class TestLocationTier:
    def test_hotspots(self):
        cases = [
            "Washington, DC", "Washington DC", "Washington, D.C. (Hybrid)",
            "Arlington, VA", "Tysons Corner, VA", "McLean, VA", "Reston, VA",
            "Bethesda, MD", "Germantown, MD", "Baltimore, Maryland",
            "New York, NY", "New York City, NY", "Santa Clara, CA",
            "San Francisco Bay Area",
        ]
        for loc in cases:
            assert main._location_tier(loc, LOC) == "hotspot", loc

    def test_non_hotspots(self):
        for loc in ("Seattle, WA", "Austin, TX", "Vancouver, BC", ""):
            assert main._location_tier(loc, LOC) == "other", loc

    def test_remote(self):
        for loc in ("Remote — US", "US Remote", "Remote"):
            assert main._location_tier(loc, LOC) == "remote", loc


class TestOnlyMode:
    def test_drops_non_hotspots(self):
        cfg = dict(CFG)
        cfg["locations"] = {**LOC, "mode": "only"}
        hot = mk(location="Reston, VA")
        other = mk(location="Austin, TX")
        remote = mk(location="Remote — US")
        assert main.categorize(hot, cfg, {}) is not None
        assert main.categorize(other, cfg, {}) is None
        assert main.categorize(remote, cfg, {}) is not None

    def test_prefer_mode_keeps_all(self):
        assert main.categorize(mk(location="Austin, TX"), CFG, {}) is not None


class TestRemoteUsSurvival:
    def test_remote_us_strings_kept(self):
        for loc in ("Remote", "Remote — US", "US Remote", "Remote (US)", "Worldwide", "Anywhere"):
            assert not main.is_non_us_location(loc), loc

    def test_remote_foreign_dropped(self):
        for loc in ("Remote — Germany", "Remote, Canada", "Remote (Amsterdam)"):
            assert main.is_non_us_location(loc), loc


class TestEmbedRendering:
    TYPE_META = {"label": "✅ Verified Entry-Level", "color": 0x1ABC9C}
    CAT_META = {"label": "🤝 Solutions / Sales Engineering"}

    def test_embed_structure(self):
        p = replace(
            mk(location="Reston, VA"),
            hotspot=True,
            verdict={
                "verified": True, "years": 1, "salary": "$120k-$150k",
                "fit_score": 4, "reason": "junior role, strong match",
            },
            description=(
                "Acme builds widgets. About us: we are the best. "
                "What you'll do: build integrations and ship features."
            ),
        )
        e = notify.build_posting_embed(p, self.TYPE_META, self.CAT_META, [])
        assert e["title"].startswith("\U0001f4cd ")
        assert e["url"] == "https://x"
        names = [f["name"] for f in e["fields"]]
        assert "📍 Location" in names and "💰 Comp" in names and "🎯 Fit" in names
        assert "junior role, strong match" in e["description"]
        assert "What you'll do: build integrations" in e["description"]
        assert "About us" not in e["description"]  # boilerplate skipped
        assert "Solutions / Sales Engineering" in e["footer"]["text"]

    def test_star_prefix(self):
        p = replace(mk(location="Reston, VA"))
        e = notify.build_posting_embed(p, self.TYPE_META, self.CAT_META, ["x"])
        assert e["title"].startswith("\u2b50 ")

    def test_no_verdict_still_renders(self):
        p = mk(location="Remote")
        e = notify.build_posting_embed(p, self.TYPE_META, self.CAT_META, [])
        assert e["title"] and e["fields"]
        assert e["description"] == "—"


class TestJdExcerpt:
    def test_anchor_jump(self):
        d = "About Acme. We are great. " + "filler " * 100 + "What you'll do: ship features."
        assert notify._jd_excerpt(d).startswith("What you'll do")

    def test_no_anchor_uses_start(self):
        assert notify._jd_excerpt("Plain description starts here").startswith("Plain")

    def test_truncation(self):
        out = notify._jd_excerpt("word " * 500, limit=50)
        assert len(out) <= 60 and out.endswith("…")

    def test_empty(self):
        assert notify._jd_excerpt(None) == ""


class TestPostedAgo:
    def test_units(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        assert "m ago" in notify._posted_ago(now - timedelta(minutes=45))
        assert "h ago" in notify._posted_ago(now - timedelta(hours=3))
        assert notify._posted_ago(None) == "unknown"
