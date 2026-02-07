"""
Main adblock engine that orchestrates network blocking, cosmetic filtering,
and scriptlet injection.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .cosmetic import CosmeticFilterHandler
from .filter_lists import get_filter_list_manager
from .matcher import NetworkFilterMatcher
from .parser import RequestType, parse_all_filter_lists
from .resources import get_redirect_resource
from .scriptlets import ScriptletHandler

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page, Route

logger = logging.getLogger(__name__)

# Map Playwright resource types to our RequestType enum
PLAYWRIGHT_TYPE_MAP = {
    "document": RequestType.DOCUMENT,
    "stylesheet": RequestType.STYLESHEET,
    "image": RequestType.IMAGE,
    "media": RequestType.MEDIA,
    "font": RequestType.FONT,
    "script": RequestType.SCRIPT,
    "texttrack": RequestType.OTHER,
    "xhr": RequestType.XMLHTTPREQUEST,
    "fetch": RequestType.XMLHTTPREQUEST,
    "eventsource": RequestType.OTHER,
    "websocket": RequestType.WEBSOCKET,
    "manifest": RequestType.OTHER,
    "other": RequestType.OTHER,
}


class AdblockEngine:
    """Main adblock engine that handles network blocking and cosmetic filtering."""

    def __init__(self, list_names: list[str] | None = None) -> None:
        """Initialize the adblock engine.

        Args:
            list_names: List of filter list names to use. If None, uses defaults.
        """
        self._list_names = list_names
        self._network_matcher = NetworkFilterMatcher()
        self._cosmetic_handler = CosmeticFilterHandler()
        self._scriptlet_handler = ScriptletHandler()
        self._initialized = False
        self._init_lock = asyncio.Lock()

        # Statistics
        self._requests_checked = 0
        self._requests_blocked = 0
        self._requests_redirected = 0

    async def initialize(self) -> None:
        """Initialize the engine by loading and parsing filter lists."""
        async with self._init_lock:
            if self._initialized:
                return

            logger.debug("Initializing adblock engine...")

            # Load filter lists
            manager = get_filter_list_manager()
            lists = await manager.get_filter_lists(self._list_names)

            if not lists:
                logger.warning("No filter lists loaded, adblock disabled")
                self._initialized = True
                return

            # Parse filter lists
            parsed = parse_all_filter_lists(lists)

            # Add filters to handlers
            self._network_matcher.add_filters(parsed.network_filters)
            self._cosmetic_handler.add_filters(parsed.cosmetic_filters)
            self._scriptlet_handler.add_filters(parsed.scriptlet_filters)

            self._initialized = True
            logger.info(
                "Adblock engine initialized: %d network filters, %d cosmetic filters, %d scriptlets",
                len(parsed.network_filters),
                len(parsed.cosmetic_filters),
                len(parsed.scriptlet_filters),
            )

    async def setup_page(self, page: Page) -> None:
        """Setup adblock filtering for a page.

        This installs route handlers for network blocking and sets up
        event handlers for cosmetic/scriptlet injection on navigation.

        Args:
            page: The Playwright page to setup.
        """
        await self.initialize()

        # Install route handler for network blocking
        await page.route("**/*", self._handle_route)

        # Setup cosmetic + scriptlet injection on navigation
        page.on(
            "framenavigated",
            lambda frame: asyncio.create_task(self._on_frame_navigated(frame)),
        )

        logger.debug("Adblock setup complete for page")

    async def _handle_route(self, route: Route) -> None:
        """Handle a route (network request).

        This is called for every network request and decides whether to
        block, redirect, or allow the request.
        """
        request = route.request
        url = request.url

        # Skip non-http(s) URLs
        if not url.startswith(("http://", "https://")):
            await route.continue_()
            return

        self._requests_checked += 1

        # Get source hostname from frame
        source_hostname = None
        try:
            frame = request.frame
            if frame:
                frame_url = frame.url
                if frame_url:
                    parsed = urlparse(frame_url)
                    source_hostname = parsed.netloc.lower()
                    if ":" in source_hostname:
                        source_hostname = source_hostname.split(":")[0]
        except Exception:
            pass

        # Map resource type
        resource_type = PLAYWRIGHT_TYPE_MAP.get(request.resource_type, RequestType.OTHER)

        # Check if should block
        result = self._network_matcher.should_block(url, resource_type, source_hostname)

        if result.blocked:
            if result.redirect:
                # Serve redirect resource instead of blocking
                resource = get_redirect_resource(result.redirect)
                if resource:
                    content, content_type = resource
                    self._requests_redirected += 1
                    logger.debug("Redirecting: %s -> %s", url[:80], result.redirect)
                    try:
                        await route.fulfill(
                            body=content,
                            content_type=content_type,
                            status=200,
                        )
                        return
                    except Exception as e:
                        logger.debug("Failed to fulfill redirect: %s", e)

            # Block the request
            self._requests_blocked += 1
            logger.debug("Blocking: %s", url[:80])
            try:
                await route.abort("blockedbyclient")
            except Exception as e:
                logger.debug("Failed to abort: %s", e)
            return

        # Allow the request
        try:
            await route.continue_()
        except Exception as e:
            # Route may already be handled
            logger.debug("Failed to continue route: %s", e)

    async def _on_frame_navigated(self, frame: Frame) -> None:
        """Handle frame navigation for cosmetic/scriptlet injection."""
        # Only handle main frame
        if frame.parent_frame is not None:
            return

        try:
            page = frame.page
            url = frame.url

            if not url or not url.startswith(("http://", "https://")):
                return

            # Get hostname
            parsed = urlparse(url)
            hostname = parsed.netloc.lower()
            if ":" in hostname:
                hostname = hostname.split(":")[0]

            # Apply cosmetic filters
            await self._cosmetic_handler.apply_to_page(page, hostname)

            # Inject scriptlets
            await self._scriptlet_handler.inject_to_page(page, hostname)

        except Exception as e:
            logger.debug("Failed to apply cosmetic/scriptlet filters: %s", e)

    def get_stats(self) -> dict[str, int]:
        """Get blocking statistics."""
        return {
            "requests_checked": self._requests_checked,
            "requests_blocked": self._requests_blocked,
            "requests_redirected": self._requests_redirected,
        }


# Singleton instance
_adblock_engine: AdblockEngine | None = None
_engine_lock = asyncio.Lock()


async def get_adblock_engine(list_names: list[str] | None = None) -> AdblockEngine:
    """Get or create the singleton AdblockEngine instance.

    Args:
        list_names: List of filter list names to use. Only used on first call.

    Returns:
        The AdblockEngine instance.
    """
    global _adblock_engine

    async with _engine_lock:
        if _adblock_engine is None:
            _adblock_engine = AdblockEngine(list_names)
            await _adblock_engine.initialize()

    return _adblock_engine


def reset_adblock_engine() -> None:
    """Reset the singleton engine (for testing)."""
    global _adblock_engine
    _adblock_engine = None
