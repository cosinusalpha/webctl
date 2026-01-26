"""
Session management for webctl daemon.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import aiofiles
from playwright.async_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Frame,
    Page,
    Playwright,
    async_playwright,
)

from ..config import get_profile_dir, resolve_browser_settings, resolve_proxy_settings
from ..security.domain_policy import DomainPolicy
from .detectors.action import ActionDetector
from .detectors.auth import AuthDetector
from .detectors.cookie_banner import CookieBannerDismisser
from .detectors.view_change import ViewChangeDetector, ViewChangeEvent
from .event_emitter import EventEmitter


@dataclass
class PageInfo:
    """Information about a tracked page."""

    page_id: str
    page: Page
    url: str
    kind: Literal["tab", "popup"]
    view_detector: ViewChangeDetector | None = None
    console_logs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionState:
    """State for a browser session."""

    session_id: str
    mode: Literal["attended", "unattended"]
    profile_dir: Path
    domain_policy: DomainPolicy | None = None
    browser: Browser | None = None
    context: BrowserContext | None = None
    pages: dict[str, PageInfo] = field(default_factory=dict)
    active_page_id: str | None = None
    auto_dismiss_cookies: bool = True  # Auto-dismiss cookie banners
    _page_counter: int = 0
    _dismissed_domains: set[str] = field(default_factory=set)  # Track domains where we dismissed


class SessionManager:
    """Manages browser sessions."""

    def __init__(self, event_emitter: EventEmitter) -> None:
        self._playwright: Playwright | None = None
        self._sessions: dict[str, SessionState] = {}
        self._event_emitter = event_emitter
        self._auth_detector = AuthDetector()
        self._action_detector = ActionDetector()
        self._cookie_dismisser = CookieBannerDismisser()

    async def _ensure_playwright(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def create_session(
        self,
        session_id: str,
        mode: Literal["attended", "unattended"] = "attended",
        profile_dir: Path | None = None,
        domain_policy: DomainPolicy | None = None,
    ) -> SessionState:
        """Create a new browser session."""
        await self._ensure_playwright()
        assert self._playwright is not None

        custom_executable, _allow_global = resolve_browser_settings()

        profile_dir = profile_dir or get_profile_dir(session_id)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Launch browser
        headless = mode == "unattended"
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }

        if custom_executable:
            launch_kwargs["executable_path"] = str(custom_executable)

        # Add proxy configuration if available
        proxy_config = resolve_proxy_settings()
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        browser = await self._playwright.chromium.launch(**launch_kwargs)

        # Load saved state if exists
        state_file = profile_dir / "state.json"
        storage_state = None
        if state_file.exists():
            async with aiofiles.open(state_file) as f:
                storage_state = json.loads(await f.read())

        context = await browser.new_context(
            storage_state=storage_state, viewport={"width": 1280, "height": 720}
        )

        session = SessionState(
            session_id=session_id,
            mode=mode,
            profile_dir=profile_dir,
            domain_policy=domain_policy,
            browser=browser,
            context=context,
        )

        # Setup popup handler
        context.on(
            "page",
            lambda page: asyncio.create_task(self._on_new_page(session, page, "popup")),
        )

        page = await context.new_page()
        await self._register_page(session, page, "tab")

        self._sessions[session_id] = session
        return session

    async def _register_page(
        self, session: SessionState, page: Page, kind: Literal["tab", "popup"]
    ) -> str:
        """Register a page and return its ID."""
        session._page_counter += 1
        page_id = f"p{session._page_counter}"

        async def on_view_change(event: ViewChangeEvent) -> None:
            await self._event_emitter.emit_view_changed(
                page_id=event.page_id,
                view=event.view,
                change_type=event.change_type,
                changed_count=event.changed_count,
            )

        view_detector = ViewChangeDetector(page, page_id, on_view_change)

        page_info = PageInfo(
            page_id=page_id,
            page=page,
            url=page.url,
            kind=kind,
            view_detector=view_detector,
        )

        session.pages[page_id] = page_info

        if session.active_page_id is None:
            session.active_page_id = page_id

        # Setup event handlers
        page.on(
            "close",
            lambda _page: asyncio.create_task(self._on_page_closed(session, page_id)),
        )

        page.on(
            "framenavigated",
            lambda frame: asyncio.create_task(self._on_navigation(session, page_id, frame)),
        )

        page.on(
            "console",
            lambda msg: self._on_console_message(page_info, msg),
        )

        # Start view change monitoring
        await view_detector.start()

        # Emit page.opened event
        await self._event_emitter.emit_page_opened(page_id, page.url, kind)

        return page_id

    async def _on_new_page(
        self, session: SessionState, page: Page, kind: Literal["tab", "popup"]
    ) -> None:
        """Handle new page/popup."""
        page_id = await self._register_page(session, page, kind)

        # Check if it's an auth popup
        if self._auth_detector.is_auth_popup(page.url):
            result = await self._auth_detector.detect(page)
            if result.detected:
                await self._event_emitter.emit_auth_required(
                    page_id=page_id,
                    kind=result.kind,
                    provider=result.provider,
                    url=page.url,
                )

    async def _on_page_closed(self, session: SessionState, page_id: str) -> None:
        """Handle page close."""
        if page_id in session.pages:
            page_info = session.pages[page_id]

            # Stop view detector
            if page_info.view_detector:
                await page_info.view_detector.stop()

            del session.pages[page_id]

        if session.active_page_id == page_id:
            # Switch to another page if available
            if session.pages:
                session.active_page_id = next(iter(session.pages))
            else:
                session.active_page_id = None

        # Emit page.closed event
        await self._event_emitter.emit_page_closed(page_id)

    async def _on_navigation(self, session: SessionState, page_id: str, frame: Frame) -> None:
        """Handle navigation events."""
        if frame != frame.page.main_frame:
            return  # Only track main frame navigations

        page_info = session.pages.get(page_id)
        if not page_info:
            return

        url = frame.url
        page_info.url = url

        # Auto-dismiss cookie banners if enabled
        if session.auto_dismiss_cookies:
            await self._try_dismiss_cookies(session, page_info.page, url)

        # Check for auth requirement (including CAPTCHA)
        auth_result = await self._auth_detector.detect(page_info.page)
        if auth_result.detected:
            await self._event_emitter.emit_auth_required(
                page_id=page_id,
                kind=auth_result.kind,
                provider=auth_result.provider,
                url=url,
            )
            # If human interaction is required (CAPTCHA), don't check for other actions
            if auth_result.requires_human:
                return

        # Check for user action requirement
        action_result = await self._action_detector.detect(page_info.page)
        if action_result.detected:
            await self._event_emitter.emit_user_action_required(
                page_id=page_id,
                kind=action_result.kind,
                description=action_result.description,
                selector_hint=action_result.selector_hint,
            )

    async def _try_dismiss_cookies(self, session: SessionState, page: Page, url: str) -> None:
        """Attempt to dismiss cookie banner for the current page."""
        try:
            # Extract domain from URL
            from urllib.parse import urlparse

            domain = urlparse(url).netloc

            # Skip if we already dismissed for this domain in this session
            if domain in session._dismissed_domains:
                return

            # Wait a moment for cookie banners to appear
            await asyncio.sleep(0.5)

            # Try to dismiss
            result = await self._cookie_dismisser.detect_and_dismiss(page)

            if result.dismissed:
                # Remember that we dismissed for this domain
                session._dismissed_domains.add(domain)
        except Exception:
            pass  # Don't fail navigation if cookie dismissal fails

    def set_active_page(self, session_id: str, page_id: str) -> bool:
        """Set the active page and emit page.focused event."""
        session = self.get_session(session_id)
        if not session or page_id not in session.pages:
            return False

        old_active = session.active_page_id
        session.active_page_id = page_id

        if old_active != page_id:
            page_info = session.pages[page_id]
            asyncio.create_task(self._event_emitter.emit_page_focused(page_id, page_info.url))

        return True

    def _on_console_message(self, page_info: PageInfo, msg: ConsoleMessage) -> None:
        """Handle console message from page."""
        location = msg.location
        location_str = None
        if location:
            url = location.get("url", "")
            line = location.get("lineNumber", "")
            location_str = f"{url}:{line}" if url else None

        log_entry: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "level": msg.type,
            "text": msg.text,
            "location": location_str,
        }
        page_info.console_logs.append(log_entry)

        # Keep last 1000 logs
        if len(page_info.console_logs) > 1000:
            page_info.console_logs = page_info.console_logs[-1000:]

    def get_session(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def get_active_page(self, session_id: str) -> Page | None:
        session = self.get_session(session_id)
        if session and session.active_page_id:
            page_info = session.pages.get(session.active_page_id)
            return page_info.page if page_info else None
        return None

    def get_active_page_info(self, session_id: str) -> PageInfo | None:
        session = self.get_session(session_id)
        if session and session.active_page_id:
            return session.pages.get(session.active_page_id)
        return None

    def get_active_page_id(self, session_id: str) -> str | None:
        session = self.get_session(session_id)
        return session.active_page_id if session else None

    def list_sessions(self) -> list[str]:
        """List all active session IDs."""
        return list(self._sessions.keys())

    def list_pages(self, session_id: str) -> list[dict[str, Any]]:
        """List all pages in a session."""
        session = self.get_session(session_id)
        if not session:
            return []

        return [
            {
                "page_id": info.page_id,
                "url": info.url,
                "kind": info.kind,
                "active": info.page_id == session.active_page_id,
            }
            for info in session.pages.values()
        ]

    async def save_session(self, session_id: str) -> None:
        """Persist session state to disk."""
        session = self.get_session(session_id)
        if session and session.context:
            state = await session.context.storage_state()
            state_file = session.profile_dir / "state.json"
            async with aiofiles.open(state_file, "w") as f:
                await f.write(json.dumps(state, indent=2))

    async def close_session(self, session_id: str) -> None:
        """Close a session and cleanup resources."""
        session = self._sessions.pop(session_id, None)
        if session:
            # Stop all view detectors
            for page_info in session.pages.values():
                if page_info.view_detector:
                    await page_info.view_detector.stop()

            await self.save_session(session_id)
            if session.context:
                await session.context.close()
            if session.browser:
                await session.browser.close()

    async def close_all(self) -> None:
        """Close all sessions."""
        for session_id in list(self._sessions.keys()):
            await self.close_session(session_id)
        if self._playwright:
            await self._playwright.stop()
