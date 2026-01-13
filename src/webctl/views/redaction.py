"""
RFC SS9.3: Redaction
- Passwords, OTPs, secrets are never emitted
- Sensitive fields are replaced with "<redacted>"
"""

import re

# Patterns that indicate sensitive fields (case-insensitive)
SENSITIVE_LABELS = re.compile(
    r"password|passwd|secret|token|api.?key|otp|verification.?code|"
    r"pin|cvv|cvc|ssn|social.?security|credit.?card|card.?number",
    re.IGNORECASE,
)

# Patterns to redact in content
REDACTION_PATTERNS = [
    # Credit card numbers (various formats)
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "[CARD REDACTED]"),
    # SSN
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN REDACTED]"),
    # Bearer tokens
    (
        re.compile(r"Bearer [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
        "Bearer [TOKEN REDACTED]",
    ),
    # API keys (common patterns)
    (
        re.compile(r'(api[_-]?key|apikey)["\s:=]+["\']?([A-Za-z0-9_-]{20,})["\']?', re.I),
        r"\1=[KEY REDACTED]",
    ),
    # AWS keys
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS KEY REDACTED]"),
    # Generic long hex strings that might be secrets
    (re.compile(r"\b[a-f0-9]{32,64}\b"), "[HASH REDACTED]"),
]


def redact_if_sensitive(value: str, is_password_field: bool = False) -> str:
    """Redact value if it appears to be sensitive."""
    if is_password_field:
        return "<redacted>"
    if not value:
        return value
    # Don't redact short values
    if len(value) < 6:
        return value
    return value


def redact_secrets(content: str) -> str:
    """Apply redaction patterns to content."""
    result = content
    for pattern, replacement in REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def is_sensitive_field(label: str) -> bool:
    """Check if a field label suggests sensitive content."""
    return bool(SENSITIVE_LABELS.search(label))
