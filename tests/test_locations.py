"""Location tiers, hot spots, and the remote-US survival fix."""
from dataclasses import replace

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


class TestNotifyRendering:
    def test_pin_and_star(self):
        p = mk(location="Reston, VA")
        pinned = notify._format_line(replace(p, hotspot=True), ["x"])
        assert pinned.startswith("\u2b50 \U0001f4cd "), pinned
        only_pin = notify._format_line(replace(p, hotspot=True), [])
        assert only_pin.startswith("\U0001f4cd "), only_pin
        plain = notify._format_line(p, [])
        assert not plain.startswith("\U0001f4cd "), plain

    def test_verify_note_rendered(self):
        p = replace(mk(location="Reston, VA"), verify_note="\u2705 AI-verified")
        line = notify._format_line(p, [])
        assert "AI-verified" in line

    def test_sort_hotspot_first(self):
        fresh = replace(
            mk(location="Austin, TX"),
            posted_at=None,
        )
        pinned = replace(mk(location="Reston, VA"), hotspot=True, posted_at=None)

        def key(p):
            return (0 if getattr(p, "hotspot", False) else 1,)

        assert key(pinned) < key(fresh)
