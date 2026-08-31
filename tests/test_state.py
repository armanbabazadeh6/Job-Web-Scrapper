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
        assert main._verify_note({"verified": True, "years": 0, "salary": None}) == "\u2705 AI-verified \u00b7 new-grad friendly"
        assert main._verify_note({"verified": True, "years": 2, "salary": "$25-30/hr"}) == "\u2705 AI-verified \u00b7 ~2 yrs exp \u00b7 $25-30/hr"
        assert main._verify_note({"verified": False}) == "\u26a0\ufe0f not AI-verified"

    def test_fit_and_clearance(self):
        v = {"verified": True, "years": 1, "fit_score": 4, "clearance_required": True}
        assert main._verify_note(v) == "\u2705 AI-verified \u00b7 ~1 yrs exp \u00b7 fit 4/5 \u00b7 \U0001f510 clearance req"
        v2 = {"verified": True, "years": 0, "fit_score": 5}
        assert main._verify_note(v2) == "\u2705 AI-verified \u00b7 new-grad friendly \u00b7 fit 5/5"
        assert "\U0001f510" not in main._verify_note({"verified": True, "years": 0})


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
