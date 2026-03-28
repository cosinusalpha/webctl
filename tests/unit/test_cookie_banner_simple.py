"""
Tests for simplified CSS-based cookie banner dismisser.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from webctl.daemon.detectors.cookie_banner import (
    CookieBannerDismisser,
    CookieBannerResult,
    _ACCEPT_SELECTORS,
    dismiss_cookie_banner,
)


def _make_page(matching_selector: str | None = None):
    """Create a mock page where one selector matches."""
    page = MagicMock()

    def make_locator(selector):
        loc = MagicMock()
        if matching_selector and selector == matching_selector:
            loc.count = AsyncMock(return_value=1)
            loc.first = MagicMock()
            loc.first.click = AsyncMock()
        else:
            loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = make_locator
    return page


class TestCookieBannerDismisser:
    """Test CSS selector cookie banner dismissal."""

    @pytest.mark.asyncio
    async def test_dismisses_onetrust(self):
        page = _make_page("#onetrust-accept-btn-handler")
        dismisser = CookieBannerDismisser()
        result = await dismisser.detect_and_dismiss(page)
        assert result.detected
        assert result.dismissed
        assert result.method == "css_selector_click"
        assert result.details["selector"] == "#onetrust-accept-btn-handler"

    @pytest.mark.asyncio
    async def test_dismisses_amazon(self):
        page = _make_page("#sp-cc-accept")
        dismisser = CookieBannerDismisser()
        result = await dismisser.detect_and_dismiss(page)
        assert result.detected
        assert result.dismissed
        assert result.details["selector"] == "#sp-cc-accept"

    @pytest.mark.asyncio
    async def test_no_banner_found(self):
        page = _make_page(None)
        dismisser = CookieBannerDismisser()
        result = await dismisser.detect_and_dismiss(page)
        assert not result.detected
        assert not result.dismissed
        assert result.method is None

    @pytest.mark.asyncio
    async def test_click_failure_continues(self):
        """If clicking raises, try next selector."""
        page = MagicMock()
        call_count = 0

        def make_locator(selector):
            nonlocal call_count
            loc = MagicMock()
            if selector == "#onetrust-accept-btn-handler":
                loc.count = AsyncMock(return_value=1)
                loc.first = MagicMock()
                loc.first.click = AsyncMock(side_effect=Exception("click failed"))
                call_count += 1
            elif selector == "#accept-recommended-btn-handler":
                loc.count = AsyncMock(return_value=1)
                loc.first = MagicMock()
                loc.first.click = AsyncMock()
                call_count += 1
            else:
                loc.count = AsyncMock(return_value=0)
            return loc

        page.locator = make_locator
        dismisser = CookieBannerDismisser()
        result = await dismisser.detect_and_dismiss(page)
        assert result.dismissed
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_convenience_function(self):
        page = _make_page("#didomi-notice-agree-button")
        result = await dismiss_cookie_banner(page)
        assert result.dismissed

    def test_selectors_not_empty(self):
        assert len(_ACCEPT_SELECTORS) > 15

    def test_result_dataclass(self):
        r = CookieBannerResult(detected=True, dismissed=True, method="test", details={})
        assert r.detected
        assert r.dismissed
