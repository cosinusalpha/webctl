"""
RFC SS11: auth.required event

Detect authentication requirements:
- SSO redirects
- OAuth popups
- MFA prompts
- Login forms
- CAPTCHAs and human verification challenges
"""

import re
from dataclasses import dataclass
from typing import Any, Literal, cast

from playwright.async_api import Page

# Type alias for auth kinds
AuthKind = Literal["sso", "mfa", "oauth", "login", "captcha", "unknown"]


@dataclass
class AuthDetectionResult:
    """Result of authentication detection."""

    detected: bool
    kind: AuthKind
    provider: str | None
    confidence: float  # 0.0 - 1.0
    details: dict[str, Any]
    requires_human: bool = False  # True if human interaction is required


# URL patterns for known auth providers
AUTH_URL_PATTERNS = [
    # SSO Providers
    (r"login\.microsoftonline\.com", "sso", "microsoft"),
    (r"accounts\.google\.com", "oauth", "google"),
    (r"github\.com/login", "oauth", "github"),
    (r"auth0\.com", "sso", "auth0"),
    (r"okta\.com", "sso", "okta"),
    (r"onelogin\.com", "sso", "onelogin"),
    (r"ping(one|identity)", "sso", "ping"),
    (r"duosecurity\.com", "mfa", "duo"),
    (r"authy\.com", "mfa", "authy"),
    # CAPTCHA providers
    (r"google\.com/recaptcha", "captcha", "recaptcha"),
    (r"hcaptcha\.com", "captcha", "hcaptcha"),
    (r"cloudflare\.com.*challenge", "captcha", "cloudflare"),
    (r"arkoselabs\.com", "captcha", "arkose"),
    (r"funcaptcha\.com", "captcha", "funcaptcha"),
    # Generic patterns
    (r"/oauth2?/", "oauth", None),
    (r"/saml/", "sso", None),
    (r"/sso/", "sso", None),
    (r"/login", "login", None),
    (r"/signin", "login", None),
    (r"/auth/", "login", None),
]

# CAPTCHA/Human verification content patterns (must be specific to avoid false positives)
CAPTCHA_CONTENT_PATTERNS = [
    r"\bi('|&#x27;)?m not a robot\b",
    r"\bverify (that )?you('|&#x27;)?re (a )?human\b",
    r"\bhuman verification required\b",
    r"\bare you a robot\?\b",
    r"\bprove you('|&#x27;)?re not a robot\b",
    r"\bcomplete (the|this) security check\b",
    r"\bsolve (the|this) captcha\b",
    r"\bunusual traffic from your (computer|network|device)\b",
    r"\bautomated queries (from|detected)\b",
    r"\bplease (click|press|tap).*to (continue|verify|proceed)\b",
    r"\bbestätigen.*dass Sie.*kein Roboter\b",  # German
    r"\bungewöhnlichen.*Datenverkehr.*erkannt\b",  # German - unusual traffic detected
    r"\bchallenge[- ]?page\b",
    r"\bcloudflare.*(is )?checking (your )?browser\b",
    r"\bjust a moment\.\.\.\b",
    r"\bddos[- ]?protection by\b",
]

# Page content patterns for MFA (must be specific to avoid false positives)
MFA_CONTENT_PATTERNS = [
    r"\benter\s+(your\s+)?verification\s*code\b",
    r"\bone[- ]?time\s*(password|code)\b",
    r"\b(open\s+your\s+)?authenticator\s*app\b",
    r"\btwo[- ]?(factor|step)\s*(auth|verification|code)\b",
    r"\benable\s+(2fa|mfa)\b",
    r"\benter\s+(the\s+)?(security|sms)\s*code\b",
    r"\bcode\s*(we\s+)?sent\s+(to\s+your|you)\b",
]


class AuthDetector:
    """Detect authentication requirements on pages."""

    def __init__(self) -> None:
        self._url_patterns = [
            (re.compile(p, re.I), kind, provider) for p, kind, provider in AUTH_URL_PATTERNS
        ]
        self._mfa_patterns = [re.compile(p, re.I) for p in MFA_CONTENT_PATTERNS]
        self._captcha_patterns = [re.compile(p, re.I) for p in CAPTCHA_CONTENT_PATTERNS]

    async def detect(self, page: Page) -> AuthDetectionResult:
        """Detect if page requires authentication or human verification."""
        url = page.url
        confidence = 0.0
        kind: AuthKind = "unknown"
        provider: str | None = None
        details: dict[str, Any] = {}
        requires_human = False

        # Check URL patterns
        for pattern, url_kind, url_provider in self._url_patterns:
            if pattern.search(url):
                # url_kind is from AUTH_URL_PATTERNS which only has valid kind values
                kind = cast(AuthKind, url_kind)
                provider = url_provider
                confidence = 0.7
                details["url_match"] = pattern.pattern
                if url_kind == "captcha":
                    requires_human = True
                break

        # Check page content for CAPTCHA indicators (higher priority)
        try:
            content = await page.content()
            for pattern in self._captcha_patterns:
                if pattern.search(content):
                    kind = "captcha"
                    requires_human = True
                    confidence = max(confidence, 0.9)
                    details["captcha_indicator"] = pattern.pattern
                    break
        except Exception:
            pass

        # Check page content for MFA indicators
        if kind != "captcha":
            try:
                content = await page.content()
                for pattern in self._mfa_patterns:
                    if pattern.search(content):
                        if kind == "unknown":
                            kind = "mfa"
                        elif kind == "login":
                            kind = "mfa"  # Upgrade login to MFA if indicators found
                        confidence = max(confidence, 0.8)
                        details["mfa_indicator"] = pattern.pattern
                        break
            except Exception:
                pass

        # Check a11y tree for auth elements
        try:
            snapshot_str = await page.locator("body").aria_snapshot()
            if snapshot_str:
                auth_elements = self._find_auth_elements_from_string(snapshot_str)
                if auth_elements:
                    if kind == "unknown":
                        kind = "login"
                    confidence = max(confidence, 0.6)
                    details["auth_elements"] = auth_elements

                # Also check for CAPTCHA elements in a11y tree
                captcha_elements = self._find_captcha_elements_from_string(snapshot_str)
                if captcha_elements:
                    kind = "captcha"
                    requires_human = True
                    confidence = max(confidence, 0.85)
                    details["captcha_elements"] = captcha_elements
        except Exception:
            pass

        return AuthDetectionResult(
            detected=confidence >= 0.5,
            kind=kind,
            provider=provider,
            confidence=confidence,
            details=details,
            requires_human=requires_human,
        )

    def _find_auth_elements_from_string(self, snapshot_str: str) -> list[dict[str, str]]:
        """Find authentication-related elements from aria_snapshot string."""
        found: list[dict[str, str]] = []

        # Check for password field indicators
        if re.search(r"textbox.*password", snapshot_str, re.I):
            found.append({"type": "password_field"})

        # Check for login/submit buttons
        login_match = re.search(
            r'button\s+"([^"]*(?:sign\s*in|log\s*in|submit|continue)[^"]*)"', snapshot_str, re.I
        )
        if login_match:
            found.append({"type": "login_button", "name": login_match.group(1)})

        # Check for OTP/code inputs
        otp_match = re.search(
            r'textbox\s+"([^"]*(?:code|otp|verification|token)[^"]*)"', snapshot_str, re.I
        )
        if otp_match:
            found.append({"type": "otp_field", "name": otp_match.group(1)})

        return found

    def _find_captcha_elements_from_string(self, snapshot_str: str) -> list[dict[str, str]]:
        """Find CAPTCHA-related elements from aria_snapshot string."""
        found: list[dict[str, str]] = []

        # Check for reCAPTCHA checkbox - must have "not a robot" or similar
        if re.search(r'checkbox.*"[^"]*not a robot[^"]*"', snapshot_str, re.I):
            found.append({"type": "recaptcha_checkbox"})

        # Check for CAPTCHA iframe - specific providers only
        if re.search(r'iframe.*"[^"]*(?:recaptcha|hcaptcha)[^"]*"', snapshot_str, re.I):
            found.append({"type": "captcha_iframe"})

        # Check for "I'm not a robot" or similar buttons/checkboxes - very specific
        verify_match = re.search(
            r'(?:button|checkbox)\s+"([^"]*(?:not a robot|I\'m human|verify I\'m not)[^"]*)"',
            snapshot_str,
            re.I,
        )
        if verify_match:
            found.append({"type": "verify_button", "name": verify_match.group(1)})

        return found

    def _find_auth_elements(
        self, tree: dict[str, Any], found: list[dict[str, str]] | None = None
    ) -> list[dict[str, str]]:
        """Find authentication-related elements in a11y tree (legacy dict format)."""
        found = found if found is not None else []

        role = tree.get("role")
        name = tree.get("name", "")

        # Check for password field
        if role == "textbox" and tree.get("valueIsPassword"):
            found.append({"type": "password_field", "name": name})

        # Check for login/submit buttons
        if role == "button":
            if re.search(r"sign\s*in|log\s*in|submit|continue", name, re.I):
                found.append({"type": "login_button", "name": name})

        # Check for OTP/code inputs
        if role == "textbox" and re.search(r"code|otp|verification|token", name, re.I):
            found.append({"type": "otp_field", "name": name})

        # Recurse
        for child in tree.get("children", []):
            self._find_auth_elements(child, found)

        return found

    def is_auth_popup(self, url: str) -> bool:
        """Quick check if URL is likely an auth popup."""
        return any(pattern.search(url) for pattern, _, _ in self._url_patterns)
