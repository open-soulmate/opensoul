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
    }

    def __init__(self, custom_patterns: dict | None = None):
        self.patterns = dict(self.PATTERNS)
        if custom_patterns:
            self.patterns.update(custom_patterns)

    def moderate(self, text: str) -> ModerationResult:
        """Scan text for sensitive content."""
        findings = []
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        max_risk = "low"

        for name, config in self.patterns.items():
            matches = re.finditer(config["pattern"], text)
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

    @staticmethod
    def _redact(text: str, findings: list[dict]) -> str:
        """Replace sensitive data with masked tokens."""
        if not findings:
            return text

        # Sort by position descending to replace from end
        sorted_findings = sorted(findings, key=lambda f: f["position"][0], reverse=True)
        result = text
        for f in sorted_findings:
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
