"""Integration tests for the verify pipeline — the LLM is mocked, no network."""
import llm_verify
from scrapers import Posting

CFG = {
    "enabled": True,
    "api_key_env": "LLM_API_KEY",
    "batch_size": 2,
    "max_per_run": 50,
    "max_years": 2,
    "hard_reject_years": 4,
    "fallback": "lenient",
    "model": ["m1"],
    "profile": {"graduation": "December 2026"},
}


def mk(i, desc="Requires 1 year of experience. Junior friendly."):
    return Posting(
        company=f"C{i}", role="Software Engineer", location="Remote",
        url=f"https://x/{i}", source="Ashby:c", description=desc,
    )


def _mock_llm(monkeypatch, fn):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_verify, "_call_llm", fn)
    # no sleeps, no model probing — keep the suite fast and offline
    monkeypatch.setattr(llm_verify.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm_verify, "_list_models", lambda *a: ["m1"])


class TestLlmFlow:
    def test_batching_and_verdicts(self, monkeypatch):
        batches = []

        def fake_call(base_url, model, api_key, items, max_years, profile_json):
            batches.append([i["id"] for i in items])
            assert "December 2026" in profile_json
            return [
                {
                    "id": i["id"], "entry_level": True, "years_required": 0,
                    "seniority": "new_grad", "salary": "$100k",
                    "clearance_required": False, "fit_score": 4,
                    "reason": "junior role",
                }
                for i in items
            ]

        _mock_llm(monkeypatch, fake_call)
        postings = [mk(i) for i in range(4)]
        results, verdicts = llm_verify.verify_postings(postings, {}, CFG, {})

        assert len(batches) == 2  # batch_size 2 -> two calls
        assert all(r["entry_level"] and r["verified"] for r in results.values())
        assert all(r["fit_score"] == 4 for r in results.values())
        assert all(r["salary"] == "$100k" for r in results.values())
        assert all(r["clearance_required"] is False for r in results.values())

    def test_verdict_cache_prevents_recall(self, monkeypatch):
        batches = []

        def fake_call(*args, **kwargs):
            batches.append(1)
            return []

        _mock_llm(monkeypatch, fake_call)
        postings = [mk(i) for i in range(2)]
        _, verdicts = llm_verify.verify_postings(postings, {}, CFG, {})
        assert len(batches) == 1

        # second run with the populated cache: zero new calls
        results2, _ = llm_verify.verify_postings(postings, {}, CFG, dict(verdicts))
        assert len(batches) == 1
        assert len(results2) == 2

    def test_failure_falls_back_lenient(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("HTTP 503 [m1]: down")

        _mock_llm(monkeypatch, boom)
        p = mk(1)
        results, _ = llm_verify.verify_postings([p], {}, CFG, {})
        r = results[p.id]
        assert r["entry_level"] is True and r["verified"] is False

    def test_regex_hard_reject_skips_llm(self, monkeypatch):
        called = []
        _mock_llm(monkeypatch, lambda *a, **k: called.append(1))
        p = mk(1, desc="Requires 8+ years of experience leading teams.")
        results, _ = llm_verify.verify_postings([p], {}, CFG, {})
        assert results[p.id]["entry_level"] is False
        assert not called

    def test_no_description_rejected(self, monkeypatch):
        _mock_llm(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
        p = mk(1, desc=None)
        results, _ = llm_verify.verify_postings([p], {}, CFG, {})
        assert results[p.id]["entry_level"] is False
        assert results[p.id]["reason"] == "no description available"
