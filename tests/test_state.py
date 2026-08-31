"""State management: seen.json pruning, verify notes, HTML conversion."""
from datetime import datetime, timezone

import main
from scrapers import html_to_text


class TestPrune:
    def test_prunes_by_date_kind(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc).date()
        rel = {
            "dated-old": {"first_seen": (now - timedelta(days=40)).isoformat(), "posted_at": "x"},
            "undated-old": {"first_seen": (now - timedelta(days=95)).isoformat()},
            "dated-new": {"first_seen": (now - timedelta(days=5)).isoformat(), "posted_at": "x"},
            "undated-mid": {"first_seen": (now - timedelta(days=40)).isoformat()},
        }
        kept = main._prune_seen(rel)
        assert set(kept) == {"dated-new", "undated-mid"}, kept.keys()


class TestVerifyNote:
    def test_variants(self):
        assert main._verify_note({"verified": True, "years": 0}) == "verified 0y"
        assert main._verify_note({"verified": True, "years": 2, "salary": "$25-30/hr"}) == "verified 2y · $25-30/hr"
        assert main._verify_note({"verified": False}) == "unverified"

    def test_fit_and_clearance(self):
        v = {"verified": True, "years": 1, "fit_score": 4, "clearance_required": True}
        assert main._verify_note(v) == "verified 1y · fit 4/5 · clearance"


class TestHtmlToText:
    def test_basic(self):
        t = html_to_text("<p>Hello <b>world</b></p><ul><li>x</li></ul>")
        assert "Hello world" in t and "x" in t

    def test_empty(self):
        assert html_to_text("") == ""


class TestRfc822Dates:
    def test_parse(self):
        from scrapers import _parse_rfc822
        assert _parse_rfc822("Mon, 31 Aug 2026 10:00:00 GMT") is not None
        assert _parse_rfc822("garbage") is None
        assert _parse_rfc822("") is None
