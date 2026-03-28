"""
Custom network idle detection that ignores media/websocket/eventsource.

Playwright's built-in networkidle waits for 0 inflight connections for 500ms,
but can't filter by resource type. This detector tracks requests manually and
ignores streaming resources so pages with video/websockets don't block loading.
"""

import asyncio

from playwright.async_api import Page, Request

# Resource types that should not block idle detection
_IGNORED_RESOURCE_TYPES = frozenset({"media", "websocket", "eventsource"})


class NetworkIdleDetector:
    """Track inflight network requests, ignoring streaming resource types."""

    def __init__(self, page: Page, idle_ms: int = 500) -> None:
        self._page = page
        self._idle_ms = idle_ms
        self._inflight: set[Request] = set()
        self._idle_timer: asyncio.TimerHandle | None = None
        self._idle_future: asyncio.Future[None] | None = None
        self._disposed = False

        page.on("request", self._on_request)
        page.on("requestfinished", self._on_request_done)
        page.on("requestfailed", self._on_request_done)

    def _on_request(self, request: Request) -> None:
        if request.resource_type in _IGNORED_RESOURCE_TYPES:
            return
        self._inflight.add(request)
        self._cancel_timer()

    def _on_request_done(self, request: Request) -> None:
        self._inflight.discard(request)
        if not self._inflight:
            self._start_timer()

    def _cancel_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _start_timer(self) -> None:
        self._cancel_timer()
        loop = asyncio.get_event_loop()
        self._idle_timer = loop.call_later(
            self._idle_ms / 1000.0, self._fire_idle
        )

    def _fire_idle(self) -> None:
        self._idle_timer = None
        if self._idle_future and not self._idle_future.done():
            self._idle_future.set_result(None)

    async def wait(self, timeout_ms: int = 30000) -> None:
        """Wait until no tracked requests for idle_ms. Raises TimeoutError on timeout."""
        if not self._inflight:
            # Already idle — wait one idle period to confirm
            await asyncio.sleep(self._idle_ms / 1000.0)
            if not self._inflight:
                return

        self._idle_future = asyncio.get_event_loop().create_future()
        try:
            await asyncio.wait_for(self._idle_future, timeout=timeout_ms / 1000.0)
        except TimeoutError:
            raise TimeoutError(f"Network not idle after {timeout_ms}ms") from None
        finally:
            self._idle_future = None

    def dispose(self) -> None:
        """Remove event listeners and cancel timers."""
        if self._disposed:
            return
        self._disposed = True
        self._cancel_timer()
        self._page.remove_listener("request", self._on_request)
        self._page.remove_listener("requestfinished", self._on_request_done)
        self._page.remove_listener("requestfailed", self._on_request_done)
        if self._idle_future and not self._idle_future.done():
            self._idle_future.cancel()
