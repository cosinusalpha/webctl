"""
Cookie consent banner detector and auto-dismisser.

Automatically detects and dismisses cookie consent banners by clicking
well-known CSS selectors for common consent management platforms.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page


@dataclass
class CookieBannerResult:
    """Result of cookie banner detection."""

    detected: bool
    dismissed: bool
    method: str | None
    details: dict[str, Any]


# Well-known accept-all selectors for common CMPs
_ACCEPT_SELECTORS = [
    # OneTrust
    "#onetrust-accept-btn-handler",
    "#accept-recommended-btn-handler",
    # Cookiebot
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    # Didomi
    "#didomi-notice-agree-button",
    # Usercentrics
    "[data-testid='uc-accept-all-button']",
    # Klaro
    ".klaro .cm-btn-success",
    ".klaro .cm-btn-accept-all",
    # Osano
    ".osano-cm-accept-all",
    # Iubenda
    ".iubenda-cs-accept-btn",
    # TrustArc
    ".trustarc-agree-btn",
    "#truste-consent-button",
    # Quantcast
    ".qc-cmp2-summary-buttons button[mode='primary']",
    # Amazon
    "#sp-cc-accept",
    # Generic IDs
    "#accept-cookies",
    "#acceptAllCookies",
    "#cookie-accept",
    "#accept-all-cookies",
    "#acceptAll",
    "#consent-accept",
    "#gdpr-accept",
    "#cookie-consent-accept",
    # Generic classes
    ".cookie-accept-all",
    ".accept-cookies",
    ".consent-accept",
    ".cc-accept",
    ".cc-allow",
    ".cc-dismiss",
    # Data attributes
    "[data-cookieconsent='accept']",
    "[data-cookie-accept]",
    "[data-consent='accept']",
    # ARIA patterns (button-only to avoid false positives on article content)
    "button[aria-label*='accept' i][aria-label*='cookie' i]",
    "button[aria-label*='Accept all' i]",
    "button[aria-label*='Alle akzeptieren' i]",
    "button[aria-label*='Tout accepter' i]",
]


class CookieBannerDismisser:
    """Detect and automatically dismiss cookie consent banners."""

    async def detect_and_dismiss(self, page: Page) -> CookieBannerResult:
        """Try well-known CSS selectors to dismiss cookie banners."""
        # Try main page selectors first
        for selector in _ACCEPT_SELECTORS:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    await locator.first.click(timeout=3000)
                    await asyncio.sleep(0.5)
                    return CookieBannerResult(
                        detected=True,
                        dismissed=True,
                        method="css_selector_click",
                        details={"selector": selector},
                    )
            except Exception:
                continue

        # Try consent iframes (Sourcepoint, etc.)
        result = await self._try_iframe_consent(page)
        if result:
            return result

        return CookieBannerResult(
            detected=False,
            dismissed=False,
            method=None,
            details={},
        )

    async def _try_iframe_consent(self, page: Page) -> CookieBannerResult | None:
        """Try to find and click accept buttons inside consent iframes."""
        iframe_selectors = [
            "iframe[id*='sp_message']",  # Sourcepoint
            "iframe[title*='consent' i]",
            "iframe[title*='cookie' i]",
            "iframe[title*='privacy' i]",
        ]
        accept_texts = [
            "Einwilligen und weiter",
            "Einverstanden und weiter",
            "Alle akzeptieren",
            "Zustimmen und weiter",
            "Zustimmen",
            "Accept all",
            "Tout accepter",
            "Akzeptieren",
            "Accept",
            "Agree",
        ]
        for iframe_sel in iframe_selectors:
            try:
                # Check if iframe exists first
                if await page.locator(iframe_sel).count() == 0:
                    continue
                iframe_locator = page.frame_locator(iframe_sel)
                for text in accept_texts:
                    btn = iframe_locator.get_by_role("button", name=text, exact=False)
                    if await btn.count() > 0:
                        await btn.first.click(timeout=3000)
                        await asyncio.sleep(0.5)
                        return CookieBannerResult(
                            detected=True,
                            dismissed=True,
                            method="iframe_button_click",
                            details={"iframe": iframe_sel, "button_text": text},
                        )
            except Exception:
                continue
        return None


async def dismiss_cookie_banner(page: Page) -> CookieBannerResult:
    """Attempt to dismiss any cookie consent banner on the page."""
    dismisser = CookieBannerDismisser()
    return await dismisser.detect_and_dismiss(page)
