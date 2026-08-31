"""LLM verifier internals — no network."""
import llm_verify


class TestYearsExtraction:
    def test_simple(self):
        assert llm_verify.extract_required_years("Requires 5+ years of experience in distributed systems") == 5
        assert llm_verify.extract_required_years("Minimum 2 years of professional experience") == 2

    def test_ranges_use_minimum(self):
        assert llm_verify.extract_required_years("You bring 0-2 years of experience") == 0
        assert llm_verify.extract_required_years("Bachelor's degree; 3-5 years experience required") == 3

    def test_blurbs_ignored(self):
        assert llm_verify.extract_required_years("We have served customers for 20 years") is None
        assert llm_verify.extract_required_years("") is None


class TestRegexScreen:
    def test_verdicts(self):
        assert llm_verify.regex_screen("8+ years of experience required", 4) is False
        assert llm_verify.regex_screen("1 year of experience preferred", 4) is True
        assert llm_verify.regex_screen("No experience needed, just grit", 4) is None


class TestParseVerdicts:
    def test_fenced(self):
        v = llm_verify._parse_verdicts(
            '```json\n[{"id": "a", "entry_level": true, "years_required": 0}]\n```'
        )
        assert v[0]["id"] == "a"

    def test_with_preamble(self):
        v = llm_verify._parse_verdicts('Here you go: [{"id": "b", "entry_level": false}]')
        assert v[0]["id"] == "b"

    def test_salary_kept(self):
        v = llm_verify._parse_verdicts(
            '[{"id": "a", "entry_level": true, "salary": "$120k-$150k"}]'
        )
        assert v[0]["salary"] == "$120k-$150k"

    def test_garbage_raises(self):
        for bad in ("", "no json here", "{not an array}"):
            try:
                llm_verify._parse_verdicts(bad)
                raise AssertionError(f"should have raised: {bad!r}")
            except ValueError:
                pass


class TestCoercion:
    def test_truthy(self):
        assert llm_verify._truthy("true") and llm_verify._truthy(True) and llm_verify._truthy("yes")
        assert not llm_verify._truthy("false") and not llm_verify._truthy(False) and not llm_verify._truthy(None)

    def test_years(self):
        assert llm_verify._coerce_years("2") == 2
        assert llm_verify._coerce_years(1.0) == 1
        assert llm_verify._coerce_years(None) is None
        assert llm_verify._coerce_years("lots") is None
