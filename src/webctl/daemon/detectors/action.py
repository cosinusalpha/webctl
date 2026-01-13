"""
RFC SS11: user.action.required event

Detect situations requiring user intervention:
- CAPTCHA
- Cookie consent
- Terms of service
- Rate limiting
- Age verification
"""

import re
from dataclasses import dataclass
from typing import Literal, cast

from playwright.async_api import Page

ActionKind = Literal["captcha", "cookie_consent", "terms", "rate_limit", "age_gate", "unknown"]


@dataclass
class ActionRequiredResult:
    """Result of user action detection."""

    detected: bool
    kind: ActionKind
    description: str
    selector_hint: str | None
    confidence: float


# Content patterns for different action types
ACTION_PATTERNS: dict[ActionKind, list[str]] = {
    "captcha": [
        r"\bsolve\s*(the|this)?\s*captcha\b",
        r"\bi('|&#x27;)?m not a robot\b",
        r"\bverify\s+(that\s+)?you('|&#x27;)?re\s+(a\s+)?human\b",
        r"\brecaptcha\s*(challenge|checkbox)\b",
        r"\bhcaptcha\s*(challenge|checkbox)\b",
        r"\bcomplete\s*(the|this)?\s*security\s*check\b",
    ],
    "cookie_consent": [
        r"\bcookie\s*(policy|consent|preferences|settings)\b",
        r"\bwe use cookies\b",
        r"\baccept\s*(all\s*)?cookies\b",
        r"\bgdpr\s*consent\b",
        r"\bmanage\s*privacy\s*settings\b",
    ],
    "terms": [
        r"\baccept\s*(the\s*)?terms\s*(of\s*service|and\s*conditions)\b",
        r"\bi\s*agree\s*to\s*(the\s*)?terms\b",
        r"\baccept\s*(our\s*)?terms\b",
        r"\baccept\s*(our\s*)?privacy\s*policy\b",
        r"\baccept\s*(our\s*)?user\s*agreement\b",
    ],
    "rate_limit": [
        r"\brate\s*limit(ed|ing)?\b",
        r"\btoo\s*many\s*requests\b",
        r"\bplease\s*slow\s*down\b",
        r"\bplease\s*try\s*again\s*(later|in\s*\d+)",
        r"\btemporarily\s*blocked\b",
        r"\b429\s*(error|too\s*many)\b",
    ],
    "age_gate": [
        r"\bage\s*verification\s*required\b",
        r"\bare\s*you\s*(18|21)\s*(or\s*)?over\??\b",
        r"\benter\s*your\s*(birth\s*date|date\s*of\s*birth|age)\b",
        r"\bthis\s*(site|page)\s*contains\s*adult\s*content\b",
    ],
}

# Common selectors for dismissing these elements
SELECTOR_HINTS = {
    "cookie_consent": [
        '[data-testid*="cookie"] button',
        "#cookie-banner button",
        ".cookie-consent button",
        '[class*="cookie"] button[class*="accept"]',
        'button:has-text("Accept")',
    ],
    "captcha": [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        ".g-recaptcha",
        ".h-captcha",
    ],
}


class ActionDetector:
    """Detect user action requirements on pages."""

    def __init__(self) -> None:
        self._patterns: dict[ActionKind, list[re.Pattern[str]]] = {
            kind: [re.compile(p, re.I) for p in patterns]
            for kind, patterns in ACTION_PATTERNS.items()
        }

    async def detect(self, page: Page) -> ActionRequiredResult:
        """Detect if page requires user action."""
        try:
            content = await page.content()
        except Exception:
            return ActionRequiredResult(
                detected=False,
                kind="unknown",
                description="",
                selector_hint=None,
                confidence=0.0,
            )

        # Check each action type
        for kind, patterns in self._patterns.items():
            for pattern in patterns:
                if pattern.search(content):
                    # Try to find a selector hint
                    selector_hint = await self._find_selector(page, kind)

                    return ActionRequiredResult(
                        detected=True,
                        kind=kind,
                        description=f"Page appears to require {kind.replace('_', ' ')}",
                        selector_hint=selector_hint,
                        confidence=0.7,
                    )

        # Check for blocking overlays/modals
        has_overlay = await self._detect_blocking_overlay(page)
        if has_overlay:
            return ActionRequiredResult(
                detected=True,
                kind="unknown",
                description="Blocking overlay detected",
                selector_hint=None,
                confidence=0.5,
            )

        return ActionRequiredResult(
            detected=False,
            kind="unknown",
            description="",
            selector_hint=None,
            confidence=0.0,
        )

    async def _find_selector(self, page: Page, kind: str) -> str | None:
        """Try to find a selector for the action element."""
        hints = SELECTOR_HINTS.get(kind, [])
        for selector in hints:
            try:
                if await page.locator(selector).count() > 0:
                    return selector
            except Exception:
                pass
        return None

    async def _detect_blocking_overlay(self, page: Page) -> bool:
        """Detect if there's a modal/overlay blocking interaction."""
        result = await page.evaluate(
            """
            () => {
                // Look for fixed/absolute positioned elements covering viewport
                const elements = document.querySelectorAll('*');
                for (const el of elements) {
                    const style = getComputedStyle(el);
                    if ((style.position === 'fixed' || style.position === 'absolute') &&
                        style.zIndex > 100 &&
                        el.offsetWidth > window.innerWidth * 0.5 &&
                        el.offsetHeight > window.innerHeight * 0.5) {
                        return true;
                    }
                }
                return false;
            }
        """
        )
        return cast(bool, result)
