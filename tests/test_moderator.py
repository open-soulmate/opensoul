"""Unit tests for ContentModerator — API-key patterns + outbound message redaction.

Pattern sources: Warp secret_redaction crate (20 regexes) ported into
src/immune/moderator.py. These tests run without a live server.
"""

from src.immune.moderator import ContentModerator


class TestAPIKeyPatterns:
    def setup_method(self):
        self.mod = ContentModerator()

    def test_openai_key_detected_and_redacted(self):
        key = "sk-" + "aB1" * 16  # 48 chars after sk-
        result = self.mod.moderate(f"here is my key: {key} thanks")
        types = [f["type"] for f in result.findings]
        assert "openai_api_key" in types
        assert key not in result.redacted_text

    def test_anthropic_key_detected(self):
        key = "sk-ant-api02-" + "xY9" * 32  # ~96 chars body
        result = self.mod.moderate(key)
        assert any(f["type"] == "anthropic_api_key" for f in result.findings)

    def test_aws_access_id_detected(self):
        result = self.mod.moderate("deploy with AKIAEXAMPLE1234567890 today")
        assert any(f["type"] == "aws_access_id" for f in result.findings)
        assert result.risk_level == "critical"

    def test_github_pat_detected(self):
        pat = "ghp_" + "a1B2" * 9  # 36 chars
        result = self.mod.moderate(f"token={pat}")
        types = [f["type"] for f in result.findings]
        assert "github_classic_pat" in types

    def test_github_fine_grained_pat_detected(self):
        pat = "github_pat_" + "a1B_" * 20 + "ab"  # exactly 82 chars body
        result = self.mod.moderate(pat)
        assert any(f["type"] == "github_fine_grained_pat" for f in result.findings)

    def test_stripe_key_detected(self):
        result = self.mod.moderate("sk_live_" + "abcd1234" * 3)
        assert any(f["type"] == "stripe_key" for f in result.findings)

    def test_google_api_key_detected(self):
        key = "AIza" + "a1B2" * 8 + "abc"  # exactly 35 chars after AIza
        result = self.mod.moderate(key)
        assert any(f["type"] == "google_api_key" for f in result.findings)

    def test_jwt_detected(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        result = self.mod.moderate(jwt)
        assert any(f["type"] == "jwt" for f in result.findings)

    def test_slack_app_token_detected(self):
        token = "xapp-1-A1234567890-123456789012-abcdef1234567890"
        result = self.mod.moderate(token)
        assert any(f["type"] == "slack_app_token" for f in result.findings)

    def test_clean_text_unaffected(self):
        result = self.mod.moderate("The quick brown fox jumps over the lazy dog.")
        assert result.is_safe is True
        assert result.redacted_text == "The quick brown fox jumps over the lazy dog."

    def test_pii_still_detected(self):
        result = self.mod.moderate("call me at 13812345678")
        assert any(f["type"] == "phone_cn" for f in result.findings)

    def test_overlapping_patterns_do_not_corrupt_text(self):
        # OpenAI key also matches generic sk- rule; trailing text must survive
        key = "sk-" + "aB1" * 16
        result = self.mod.moderate(f"use {key} then continue working")
        assert key not in result.redacted_text
        assert result.redacted_text.endswith("then continue working")


class TestRedactMessages:
    def setup_method(self):
        self.mod = ContentModerator()

    def test_secret_in_user_message_redacted(self):
        key = "sk-" + "aB1" * 16
        messages = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": f"debug this key {key}"},
        ]
        out, findings = self.mod.redact_messages(messages)
        assert key not in out[1]["content"]
        assert out[0]["content"] == "you are helpful"
        assert any(f["type"] == "openai_api_key" for f in findings)

    def test_inputs_not_mutated(self):
        key = "sk-" + "aB1" * 16
        messages = [{"role": "user", "content": key}]
        self.mod.redact_messages(messages)
        assert messages[0]["content"] == key

    def test_min_risk_threshold(self):
        # low-risk PII (email) should pass at critical threshold
        messages = [{"role": "user", "content": "mail me at foo@example.com"}]
        out, findings = self.mod.redact_messages(messages, min_risk="critical")
        assert findings == []
        assert out[0]["content"] == "mail me at foo@example.com"
        # but be redacted at low threshold
        out2, findings2 = self.mod.redact_messages(messages, min_risk="low")
        assert "foo@example.com" not in out2[0]["content"]
        assert any(f["type"] == "email" for f in findings2)

    def test_non_string_content_passthrough(self):
        messages = [{"role": "user", "content": None}]
        out, findings = self.mod.redact_messages(messages)
        assert out == messages
        assert findings == []

    def test_findings_never_contain_secret(self):
        key = "sk-" + "aB1" * 16
        _, findings = self.mod.redact_messages([{"role": "user", "content": key}])
        for f in findings:
            assert key not in str(f)


class TestFailSafe:
    def test_broken_custom_pattern_does_not_disarm(self):
        mod = ContentModerator(custom_patterns={"broken": {"pattern": "([unclosed", "label": "x", "risk": "high"}})
        # broken pattern skipped at compile time, built-in patterns still armed
        key = "sk-" + "aB1" * 16
        result = mod.moderate(key)
        assert any(f["type"] == "openai_api_key" for f in result.findings)
