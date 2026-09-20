"""Content moderation — sensitive data detection and redaction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ModerationResult:
    is_safe: bool
    risk_level: str  # "low", "medium", "high", "critical"
    findings: list[dict] = field(default_factory=list)
    redacted_text: str = ""
    original_length: int = 0


class ContentModerator:
    """Detect and redact sensitive information in text."""

    # Patterns for sensitive data
    PATTERNS = {
        "phone_cn": {
            "pattern": r"(?<!\d)1[3-9]\d{9}(?!\d)",
            "label": "Chinese phone number",
            "risk": "medium",
        },
        "id_card_cn": {
            "pattern": r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)",
            "label": "Chinese ID card",
            "risk": "high",
        },
        "email": {
            "pattern": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "label": "Email address",
            "risk": "low",
        },
        "ip_address": {
            "pattern": r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
            "label": "IP address",
            "risk": "low",
        },
        "bank_card": {
            "pattern": r"(?<!\d)(?:6[0-9]{15,18}|4[0-9]{12,15}|5[1-5][0-9]{14}|3[47][0-9]{13})(?!\d)",
            "label": "Bank card number",
            "risk": "high",
        },
        "password_leak": {
            "pattern": r"(?i)(?:password|passwd|pwd|secret|token|api.?key)\s*[:=]\s*\S+",
            "label": "Password/secret in text",
            "risk": "critical",
        },
        "url_with_auth": {
            "pattern": r"https?://[^:]+:[^@]+@[^\s]+",
            "label": "URL with embedded credentials",
            "risk": "high",
        },
        # ── API key / token formats (ported from Warp secret_redaction crate, 20 regexes) ──
        # Source: ~/agent-research-src/warp-master/crates/secret_redaction/src/lib.rs
        "google_api_key": {
            "pattern": r"\bAIza[0-9A-Za-z\-_]{35}\b",
            "label": "Google API Key",
            "risk": "critical",
        },
        "aws_access_id": {
            "pattern": r"\b(AKIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{12,}\b",
            "label": "AWS Access ID",
            "risk": "critical",
        },
        "slack_app_token": {
            "pattern": r"\bxapp-[0-9]+-[A-Za-z0-9_]+-[0-9]+-[a-f0-9]+\b",
            "label": "Slack App Token",
            "risk": "critical",
        },
        "github_classic_pat": {
            "pattern": r"\bghp_[A-Za-z0-9_]{36}\b",
            "label": "GitHub Classic Personal Access Token",
            "risk": "critical",
        },
        "github_fine_grained_pat": {
            "pattern": r"\bgithub_pat_[A-Za-z0-9_]{82}\b",
            "label": "GitHub Fine-Grained Personal Access Token",
            "risk": "critical",
        },
        "github_oauth_token": {
            "pattern": r"\bgho_[A-Za-z0-9_]{36}\b",
            "label": "GitHub OAuth Access Token",
            "risk": "critical",
        },
        "github_user_to_server_token": {
            "pattern": r"\bghu_[A-Za-z0-9_]{36}\b",
            "label": "GitHub User-to-Server Token",
            "risk": "critical",
        },
        "github_server_to_server_token": {
            "pattern": r"\bghs_[A-Za-z0-9_]{36}\b",
            "label": "GitHub Server-to-Server Token",
            "risk": "critical",
        },
        "stripe_key": {
            "pattern": r"\b(?:r|s)k_(?:test|live)_[0-9a-zA-Z]{24}\b",
            "label": "Stripe Key",
            "risk": "critical",
        },
        "firebase_auth_domain": {
            "pattern": r"\b[a-z0-9-]{1,30}\.firebaseapp\.com\b",
            "label": "Firebase Auth Domain",
            "risk": "medium",
        },
        "jwt": {
            "pattern": r"\b(?:ey[A-Za-z0-9_\-]{10,}\.){2}[A-Za-z0-9_\-]{10,}\b",
            "label": "JSON Web Token",
            "risk": "high",
        },
        "openai_api_key": {
            "pattern": r"\bsk-[a-zA-Z0-9]{48}\b",
            "label": "OpenAI API Key",
            "risk": "critical",
        },
        "anthropic_api_key": {
            "pattern": r"\bsk-ant-api\d{0,2}-[a-zA-Z0-9\-]{80,120}\b",
            "label": "Anthropic API Key",
            "risk": "critical",
        },
        "generic_sk_api_key": {
            "pattern": r"\bsk-[a-zA-Z0-9\-]{10,100}\b",
            "label": "Generic sk- API Key",
            "risk": "critical",
        },
        "fireworks_api_key": {
            "pattern": r"\bfw_[a-zA-Z0-9]{24}\b",
            "label": "Fireworks API Key",
            "risk": "critical",
        },
        "ipv6_address": {
            "pattern": r"\b(?:[0-9A-Fa-f]{1,4}:){1,7}[0-9A-Fa-f]{1,4}\b",
            "label": "IPv6 Address",
            "risk": "low",
        },
        "mac_address": {
            "pattern": r"\b(?:[a-fA-F0-9]{2}[:-]){5}[a-fA-F0-9]{2}\b",
            "label": "MAC Address",
            "risk": "low",
        },
    }

    def __init__(self, custom_patterns: dict | None = None):
        self.patterns = dict(self.PATTERNS)
        if custom_patterns:
            self.patterns.update(custom_patterns)
        # Precompile all patterns once (research note #4: avoid per-call re.compile).
        # Fail-safe: a broken custom pattern is skipped, the rest stay armed
        # (Warp: "构造失败不换弹匣" — never disarm working rules over one bad one).
        self._compiled: dict[str, re.Pattern] = {}
        for name, config in self.patterns.items():
            try:
                self._compiled[name] = re.compile(config["pattern"])
            except re.error:
                continue

    def moderate(self, text: str) -> ModerationResult:
        """Scan text for sensitive content."""
        findings = []
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        max_risk = "low"

        for name, regex in self._compiled.items():
            config = self.patterns[name]
            matches = regex.finditer(text)
            for match in matches:
                findings.append(
                    {
                        "type": name,
                        "label": config["label"],
                        "risk": config["risk"],
                        "position": (match.start(), match.end()),
                        "matched": match.group(),
                    }
                )
                if risk_order.get(config["risk"], 0) > risk_order.get(max_risk, 0):
                    max_risk = config["risk"]

        is_safe = max_risk == "low" and len(findings) == 0
        redacted = self._redact(text, findings)

        return ModerationResult(
            is_safe=is_safe,
            risk_level=max_risk,
            findings=findings,
            redacted_text=redacted,
            original_length=len(text),
        )

    def redact_messages(
        self, messages: list[dict], min_risk: str = "low"
    ) -> tuple[list[dict], list[dict]]:
        """Redact secrets in an OpenAI-style chat message list (outbound LLM guard).

        Ported from Warp's blocklist layer: every text block sent to the LLM is
        scanned so API keys/PII never leave the machine. Returns a new message
        list (inputs are not mutated) plus a flat findings summary (type/risk
        only — never the matched secret itself, safe for logs).
        """
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = risk_order.get(min_risk, 0)
        out: list[dict] = []
        all_findings: list[dict] = []
        for msg in messages:
            if not isinstance(msg, dict):
                out.append(msg)
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                out.append(msg)
                continue
            result = self.moderate(content)
            actionable = [f for f in result.findings if risk_order.get(f["risk"], 0) >= threshold]
            if not actionable:
                out.append(msg)
                continue
            new_msg = dict(msg)
            new_msg["content"] = result.redacted_text
            out.append(new_msg)
            all_findings.extend(
                {"type": f["type"], "risk": f["risk"], "label": f["label"]} for f in actionable
            )
        return out, all_findings

    @staticmethod
    def _redact(text: str, findings: list[dict]) -> str:
        """Replace sensitive data with masked tokens.

        Overlapping findings are merged first (Warp merge_sorted_ranges
        pattern) — e.g. an OpenAI key also matches the generic sk- rule;
        replacing both at stale offsets would corrupt surrounding text.
        The highest-risk type wins for the merged range.
        """
        if not findings:
            return text

        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        sorted_findings = sorted(findings, key=lambda f: f["position"])
        merged: list[dict] = []
        for f in sorted_findings:
            if merged and f["position"][0] < merged[-1]["position"][1]:
                # overlap: extend end, keep the higher-risk type
                prev = merged[-1]
                prev["position"] = (prev["position"][0], max(prev["position"][1], f["position"][1]))
                if risk_order.get(f["risk"], 0) > risk_order.get(prev["risk"], 0):
                    prev["type"] = f["type"]
                    prev["risk"] = f["risk"]
            else:
                merged.append(dict(f))

        # Replace from the end to keep earlier offsets valid
        result = text
        for f in reversed(merged):
            start, end = f["position"]
            ftype = f["type"]
            original = result[start:end]
            if ftype == "phone_cn":
                replacement = original[:3] + "****" + original[-4:]
            elif ftype == "id_card_cn":
                replacement = original[:4] + "**********" + original[-4:]
            elif ftype == "email":
                parts = original.split("@")
                replacement = parts[0][:2] + "***@" + parts[1] if len(parts) == 2 else "***"
            elif ftype == "bank_card":
                replacement = original[:4] + " **** **** " + original[-4:]
            else:
                replacement = f"[REDACTED:{ftype}]"
            result = result[:start] + replacement + result[end:]

        return result
