"""
Cookie consent banner detector and auto-dismisser.

Automatically detects and dismisses cookie consent banners to prevent them
from blocking page interactions.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page


@dataclass
class CookieBannerResult:
    """Result of cookie banner detection."""

    detected: bool
    dismissed: bool
    method: str | None  # How it was dismissed
    details: dict[str, Any]


# Common cookie consent button patterns (accept/agree)
# These patterns match button names/labels - case insensitive
ACCEPT_BUTTON_PATTERNS = [
    # English - exact matches first
    r"^accept\s*all(\s*cookies)?$",
    r"^accept\s*&?\s*continue$",
    r"^i\s*accept$",
    r"^agree(\s*to)?\s*all$",
    r"^allow\s*all(\s*cookies)?$",
    r"^got\s*it!?$",
    r"^ok(ay)?$",
    r"^i\s*agree$",
    r"^yes,?\s*i\s*agree$",
    # English - partial matches
    r"accept.*cookie",
    r"accept\s*all",
    r"agree.*all",
    r"allow.*cookie",
    r"consent.*all",
    # German - exact matches first (common variations)
    r"^alle\s*akzeptieren$",
    r"^alles\s*akzeptieren$",
    r"^alle\s*cookies?\s*akzeptieren$",
    r"^akzeptieren$",
    r"^zustimmen$",
    r"^allen?\s*zustimmen$",
    r"^einverstanden$",
    r"^verstanden$",
    r"^alles\s*klar$",
    r"^ja,?\s*ich\s*stimme\s*zu$",
    r"^ich\s*stimme\s*zu$",
    r"^einwilligen$",
    r"^alle\s*auswählen$",
    # German - partial matches
    r"alle.*akzeptieren",
    r"cookies?\s*akzeptieren",
    r"akzeptieren.*alle",
    r"zustimmen.*alle",
    r"einverstanden.*alle",
    # French
    r"^accepter\s*tout$",
    r"^tout\s*accepter$",
    r"^j'accepte$",
    r"^accepter$",
    r"accepter.*tout",
    r"tout.*accepter",
    # Spanish
    r"^aceptar\s*todo$",
    r"^aceptar$",
    r"^acepto$",
    r"aceptar.*todo",
    # Dutch
    r"^alles\s*accepteren$",
    r"^accepteren$",
    r"alles.*accepteren",
    # Italian
    r"^accetta\s*tutto$",
    r"^accetta$",
    r"accetta.*tutto",
    # Portuguese
    r"^aceitar\s*tudo$",
    r"^aceitar$",
    # Polish
    r"^akceptuj\s*wszystkie$",
    r"^zaakceptuj$",
]

# Common cookie banner container patterns (for detection)
BANNER_CONTAINER_PATTERNS = [
    r"cookie",
    r"consent",
    r"gdpr",
    r"privacy",
    r"ccpa",
    r"onetrust",
    r"cookiebot",
    r"trustarc",
    r"quantcast",
    r"didomi",
    r"usercentrics",
]


class CookieBannerDismisser:
    """Detect and automatically dismiss cookie consent banners."""

    def __init__(self) -> None:
        self._accept_patterns = [re.compile(p, re.I) for p in ACCEPT_BUTTON_PATTERNS]
        self._banner_patterns = [re.compile(p, re.I) for p in BANNER_CONTAINER_PATTERNS]

    async def detect_and_dismiss(self, page: Page) -> CookieBannerResult:
        """
        Detect and automatically dismiss cookie consent banners.

        Returns result indicating whether a banner was found and dismissed.
        """
        details: dict[str, Any] = {}

        # Strategy 1: Find and click accept button via a11y tree
        try:
            snapshot_str = await page.locator("body").aria_snapshot()
            if snapshot_str:
                accept_button = self._find_accept_button(snapshot_str)
                if accept_button:
                    details["found_button"] = accept_button
                    success = await self._click_button(page, accept_button)
                    if success:
                        return CookieBannerResult(
                            detected=True,
                            dismissed=True,
                            method="a11y_button_click",
                            details=details,
                        )
        except Exception as e:
            details["a11y_error"] = str(e)

        # Strategy 2: Try common CSS selectors for cookie banners
        css_selectors = [
            # OneTrust (very common)
            "#onetrust-accept-btn-handler",
            ".onetrust-close-btn-handler",
            "#accept-recommended-btn-handler",
            "button.onetrust-accept-btn-handler",
            # Cookiebot
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "#CybotCookiebotDialogBodyButtonAccept",
            "#CybotCookiebotDialogBodyButtonDecline",
            "a#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            # TrustArc / TrustE
            ".trustarc-agree-btn",
            "#truste-consent-button",
            ".pdynamicbutton .call",
            # Quantcast
            ".qc-cmp2-summary-buttons button[mode='primary']",
            ".qc-cmp-button",
            # Didomi
            "#didomi-notice-agree-button",
            ".didomi-continue-without-agreeing",
            # Usercentrics
            "[data-testid='uc-accept-all-button']",
            "#usercentrics-root button[data-testid='uc-accept-all-button']",
            # Klaro
            ".klaro .cm-btn-success",
            ".klaro .cm-btn-accept-all",
            # Osano
            ".osano-cm-accept-all",
            # Iubenda
            ".iubenda-cs-accept-btn",
            # Generic patterns - IDs
            "#accept-cookies",
            "#acceptAllCookies",
            "#cookie-accept",
            "#cookieAccept",
            "#accept-all-cookies",
            "#acceptAll",
            "#consent-accept",
            "#gdpr-accept",
            "#cookie-consent-accept",
            "#allow-all-cookies",
            # Generic patterns - classes
            ".cookie-accept",
            ".cookie-consent-accept",
            ".accept-cookies",
            ".accept-all-cookies",
            ".js-accept-cookies",
            ".cookie-accept-all",
            ".consent-accept",
            ".gdpr-accept",
            ".cc-accept",
            ".cc-allow",
            ".cc-dismiss",
            # Data attributes
            "[data-cookieconsent='accept']",
            "[data-cookie-accept]",
            "[data-action='accept']",
            "[data-gdpr-accept]",
            "[data-consent='accept']",
            # ARIA patterns
            "[aria-label*='accept' i][aria-label*='cookie' i]",
            "[aria-label*='akzeptieren' i]",
            "[aria-label*='alle akzeptieren' i]",
            "[aria-label*='Accept all' i]",
            "[aria-label*='Agree' i]",
        ]

        for selector in css_selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    await locator.first.click(timeout=3000)
                    details["css_selector"] = selector
                    await asyncio.sleep(0.5)  # Wait for banner to disappear
                    return CookieBannerResult(
                        detected=True,
                        dismissed=True,
                        method="css_selector_click",
                        details=details,
                    )
            except Exception:
                continue

        # Strategy 3: Try to find buttons by text content
        text_patterns = [
            # English
            "Accept All",
            "Accept all cookies",
            "Accept all",
            "Accept & Continue",
            "I Accept",
            "I Agree",
            "Allow All",
            "Allow all cookies",
            "Agree",
            "Agree to all",
            "OK",
            "Got it",
            "Continue",
            # German
            "Alle akzeptieren",
            "Alles akzeptieren",
            "Alle Cookies akzeptieren",
            "Akzeptieren",
            "Zustimmen",
            "Allen zustimmen",
            "Alle zulassen",
            "Einverstanden",
            "Verstanden",
            "Alles klar",
            "Ich stimme zu",
            # French
            "Accepter tout",
            "Tout accepter",
            "J'accepte",
            # Spanish
            "Aceptar todo",
            "Aceptar todas",
            "Acepto",
            # Dutch
            "Alles accepteren",
            "Accepteren",
        ]

        for text in text_patterns:
            try:
                locator = page.get_by_role("button", name=text)
                if await locator.count() > 0:
                    await locator.first.click(timeout=3000)
                    details["button_text"] = text
                    await asyncio.sleep(0.5)
                    return CookieBannerResult(
                        detected=True,
                        dismissed=True,
                        method="button_text_click",
                        details=details,
                    )
            except Exception:
                continue

        # Strategy 4: Check if banner exists but couldn't be dismissed
        banner_detected = await self._check_banner_exists(page)

        return CookieBannerResult(
            detected=banner_detected,
            dismissed=False,
            method=None,
            details=details,
        )

    def _find_accept_button(self, snapshot_str: str) -> dict[str, str] | None:
        """Find accept/agree button in a11y snapshot."""
        # Look for buttons that match accept patterns
        button_pattern = re.compile(r'button\s+"([^"]+)"', re.I)

        for match in button_pattern.finditer(snapshot_str):
            button_name = match.group(1)
            for pattern in self._accept_patterns:
                if pattern.search(button_name):
                    return {"role": "button", "name": button_name}

        return None

    async def _click_button(self, page: Page, button: dict[str, str]) -> bool:
        """Click a button by role and name."""
        try:
            name = button.get("name")

            if name:
                locator = page.get_by_role("button", name=name)
            else:
                return False

            if await locator.count() > 0:
                await locator.first.click(timeout=5000)
                await asyncio.sleep(0.5)  # Wait for banner animation
                return True
        except Exception:
            pass
        return False

    async def _check_banner_exists(self, page: Page) -> bool:
        """Check if a cookie banner is likely present on the page."""
        try:
            # Check for common banner indicators in page content
            content = await page.content()
            for pattern in self._banner_patterns:
                if pattern.search(content):
                    # Look for visible dialog/banner elements
                    for selector in [
                        "[class*='cookie']",
                        "[id*='cookie']",
                        "[class*='consent']",
                        "[id*='consent']",
                        "[class*='gdpr']",
                    ]:
                        try:
                            locator = page.locator(selector)
                            if await locator.count() > 0:
                                if await locator.first.is_visible():
                                    return True
                        except Exception:
                            continue
        except Exception:
            pass
        return False


# Convenience function for quick dismissal
async def dismiss_cookie_banner(page: Page) -> CookieBannerResult:
    """Attempt to dismiss any cookie consent banner on the page."""
    dismisser = CookieBannerDismisser()
    return await dismisser.detect_and_dismiss(page)
