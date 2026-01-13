"""
RFC SS15: Security Considerations
- Optional domain allow/deny lists
"""

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse


@dataclass
class DomainPolicy:
    """
    Domain allow/deny policy for navigation.

    Modes:
    - "allow": Only allowed domains can be accessed (whitelist)
    - "deny": All except denied domains can be accessed (blacklist)
    - "both": Allow list takes precedence, then deny list checked
    """

    mode: Literal["allow", "deny", "both"] = "deny"
    allow_patterns: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)

    # Default dangerous domains to deny
    default_deny: list[str] = field(
        default_factory=lambda: [
            "*.malware.*",
            "*.phishing.*",
        ]
    )

    def is_allowed(self, url: str) -> tuple[bool, str]:
        """
        Check if URL is allowed by policy.

        Returns:
            (allowed: bool, reason: str)
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove port if present
            if ":" in domain:
                domain = domain.split(":")[0]

        except Exception as e:
            return False, f"Invalid URL: {e}"

        # Check default deny list first
        for pattern in self.default_deny:
            if self._match_pattern(domain, pattern):
                return False, f"Domain matches default deny pattern: {pattern}"

        if self.mode == "allow":
            # Whitelist mode: must match an allow pattern
            for pattern in self.allow_patterns:
                if self._match_pattern(domain, pattern):
                    return True, f"Domain matches allow pattern: {pattern}"
            return False, "Domain not in allow list"

        elif self.mode == "deny":
            # Blacklist mode: must not match a deny pattern
            for pattern in self.deny_patterns:
                if self._match_pattern(domain, pattern):
                    return False, f"Domain matches deny pattern: {pattern}"
            return True, "Domain not in deny list"

        else:  # "both"
            # Allow list takes precedence
            for pattern in self.allow_patterns:
                if self._match_pattern(domain, pattern):
                    return True, f"Domain matches allow pattern: {pattern}"

            # Then check deny list
            for pattern in self.deny_patterns:
                if self._match_pattern(domain, pattern):
                    return False, f"Domain matches deny pattern: {pattern}"

            return True, "Domain not matched by any pattern"

    def _match_pattern(self, domain: str, pattern: str) -> bool:
        """Match domain against glob pattern."""
        # Support wildcards: *.example.com, example.*, etc.
        pattern = pattern.lower()

        # Convert glob to regex
        if "*" in pattern or "?" in pattern:
            return fnmatch.fnmatch(domain, pattern)

        # Exact match or subdomain match
        return domain == pattern or domain.endswith("." + pattern)


@dataclass
class PolicyConfig:
    """Configuration for domain policies."""

    enabled: bool = False
    policy: DomainPolicy = field(default_factory=DomainPolicy)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyConfig":
        """Create from dictionary (e.g., from config file)."""
        if not data:
            return cls()

        policy_data = data.get("policy", {})
        policy = DomainPolicy(
            mode=policy_data.get("mode", "deny"),
            allow_patterns=policy_data.get("allow", []),
            deny_patterns=policy_data.get("deny", []),
        )

        return cls(
            enabled=data.get("enabled", False),
            policy=policy,
        )


# Example configuration:
# domain_policy:
#   enabled: true
#   policy:
#     mode: "both"
#     allow:
#       - "*.mycompany.com"
#       - "github.com"
#       - "*.github.com"
#     deny:
#       - "*.malware.com"
#       - "blocked-site.com"
