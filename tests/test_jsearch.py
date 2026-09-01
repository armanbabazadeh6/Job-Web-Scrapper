"""JSearch source: datetime parsing, mocked fetch, budget state."""
import json

import scrapers
import main
from scrapers import _parse_jsearch_datetime


class TestJSearchDatetime:
    def test_parse(self):
        from datetime import timezone
        dt = _parse_jsearch_datetime("2026-08-31 13:22:01")
        assert dt is not None and dt.tzinfo == timezone.utc
        assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 8, 31, 13)

    def test_bad(self):
        assert _parse_jsearch_datetime(None) is None
        assert _parse_jsearch_datetime("Aug 31") is None
        assert _parse_jsearch_datetime("") is None


class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestFetchJSearch:
    def _job(self, **over):
        job = {
            "employer_name": "Stripe",
            "job_title": "Software Engineer, New Grad",
            "job_apply_link": "https://jobs.example/1",
            "job_description": "<p>What you'll do: ship</p>",
            "job_posted_at_datetime_utc": "2026-08-31 10:00:00",
            "job_is_remote": False,
            "job_city": "Arlington",
            "job_state": "VA",
            "job_country": "US",
        }
        job.update(over)
        return job

    def test_no_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
        posts, host = scrapers.fetch_jsearch("q", {})
        assert posts == [] and host is None

    def test_mapping(self, monkeypatch):
        monkeypatch.setenv("JSEARCH_API_KEY", "k")
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _FakeResp({"data": [self._job()]})

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        posts, host = scrapers.fetch_jsearch("entry level swe", {"date_posted": "3days"})
        assert len(posts) == 1
        assert host == "jsearch.p.rapidapi.com"
        p = posts[0]
        assert p.company == "Stripe" and p.role == "Software Engineer, New Grad"
        assert p.location == "Arlington, VA"
        assert p.source == "JSearch"
        assert p.posted_at is not None
        assert "ship" in (p.description or "")
        assert captured["url"].endswith("/search")
        assert captured["params"]["query"] == "entry level swe"
        assert "X-RapidAPI-Key" in captured["headers"]

    def test_remote_and_missing_fields(self, monkeypatch):
        monkeypatch.setenv("JSEARCH_API_KEY", "k")
        monkeypatch.setattr(
            scrapers.requests, "get",
            lambda *a, **k: _FakeResp({"data": [self._job(
                job_is_remote=True, job_city=None, job_state=None, job_country="US"
            )]}),
        )
        posts, _ = scrapers.fetch_jsearch("q", {})
        assert posts[0].location == "Remote — US"

    def test_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv("JSEARCH_API_KEY", "k")

        def boom(*a, **k):
            raise RuntimeError("HTTP 429: quota exceeded")

        monkeypatch.setattr(scrapers.requests, "get", boom)
        posts, host = scrapers.fetch_jsearch("q", {})
        assert posts == [] and host is None

    def test_host_fallback_on_404(self, monkeypatch):
        """A 404 on the first gateway moves the chain to the next host."""
        monkeypatch.setenv("JSEARCH_API_KEY", "k")
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(url)
            if "jsearch.p" in url:
                resp = _FakeResp({})
                resp.status_code = 404
                resp.text = '{"message": "Not Found"}'
                return resp
            return _FakeResp({"data": [self._job()]})

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        posts, host = scrapers.fetch_jsearch("q", {})
        assert host == "jsearch1.p.rapidapi.com"
        assert len(posts) == 1
        assert any("jsearch1" in u for u in calls)

    def test_working_host_goes_first(self, monkeypatch):
        monkeypatch.setenv("JSEARCH_API_KEY", "k")
        seen = []

        def fake_get(url, params=None, headers=None, timeout=None):
            seen.append(url)
            return _FakeResp({"data": [self._job()]})

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        _, host = scrapers.fetch_jsearch("q", {}, working_host="jsearch2.p.rapidapi.com")
        assert host == "jsearch2.p.rapidapi.com"
        assert "jsearch2" in seen[0] and len(seen) == 1


class TestJSearchState:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "JSEARCH_STATE_PATH", tmp_path / "js.json")
        st = main._jsearch_state()
        assert st == {"date": st["date"], "count": 0, "next": 0, "host": ""}
        st["count"] = 3
        st["next"] = 1
        st["host"] = "jsearch1.p.rapidapi.com"
        main._save_jsearch_state(st)
        again = main._jsearch_state()
        assert again["count"] == 3 and again["next"] == 1
        assert again["host"] == "jsearch1.p.rapidapi.com"

    def test_stale_date_resets(self, tmp_path, monkeypatch):
        f = tmp_path / "js.json"
        f.write_text(json.dumps({"date": "2020-01-01", "count": 99, "next": 7, "host": "jsearch1.p.rapidapi.com"}))
        monkeypatch.setattr(main, "JSEARCH_STATE_PATH", f)
        st = main._jsearch_state()
        assert st["count"] == 0 and st["next"] == 0
        assert st["host"] == "jsearch1.p.rapidapi.com"  # host survives day rollovers

    def test_config(self):
        cfg = main.load_config()
        js = cfg.get("jsearch") or {}
        assert js.get("enabled") and js.get("queries")
        assert js["requests_per_day"] > 0
