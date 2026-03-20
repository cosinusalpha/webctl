"""
Navigation command handlers.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ...protocol.messages import DoneResponse, ErrorResponse, Request, Response
from ...views.a11y import A11yExtractOptions, extract_a11y_view
from ..detectors.cookie_banner import dismiss_cookie_banner
from ..event_emitter import EventEmitter
from ..session_manager import SessionManager
from .registry import register


async def _build_page_summary(page: "Page") -> dict[str, Any]:  # noqa: F821
    """Build a compact page summary with headings, top interactive elements, and markdown preview."""
    summary: dict[str, Any] = {"url": page.url, "title": await page.title()}

    # Collect compact a11y overview: headings + first interactive elements
    try:
        headings: list[str] = []
        interactive: list[str] = []
        options = A11yExtractOptions(
            include_path_hint=False,
            names_only=True,
            interesting_only=True,
        )
        async for item in extract_a11y_view(page, options):
            role = item.get("role", "")
            name = item.get("name", "")
            label = f'{role} "{name}"' if name else role
            if role == "heading" and len(headings) < 5:
                headings.append(label)
            elif role in {
                "button",
                "link",
                "textbox",
                "searchbox",
                "combobox",
                "checkbox",
            } and len(interactive) < 10:
                interactive.append(label)
            # Stop early once we have enough
            if len(headings) >= 5 and len(interactive) >= 10:
                break
        summary["headings"] = headings
        summary["interactive"] = interactive
    except Exception:
        pass

    return summary


@register("navigate")
async def handle_navigate(
    request: Request,
    session_manager: SessionManager,
    event_emitter: EventEmitter,
    **kwargs: Any,
) -> AsyncIterator[Response]:
    """Navigate to a URL."""
    url = request.args.get("url")
    session_id = request.args.get("session", "default")
    wait_until = request.args.get("wait_until", "load")

    if not url:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'url' argument",
            code="missing_argument",
        )
        return

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    # Check domain policy
    if session.domain_policy:
        allowed, reason = session.domain_policy.is_allowed(url)
        if not allowed:
            yield ErrorResponse(
                req_id=request.req_id,
                error=f"Navigation blocked: {reason}",
                code="domain_blocked",
            )
            return

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        # Emit navigation started event
        page_id = session_manager.get_active_page_id(session_id)
        await event_emitter.emit_navigation_started(url, page_id)

        # Mark page as navigating to prevent session_manager's _on_navigation
        # from racing with our cookie dismiss below
        session._navigating_pages.add(page_id)

        # Navigate
        await page.goto(url, wait_until=wait_until)

        # Auto-dismiss cookie banners (try twice — iframes may load late)
        await asyncio.sleep(1.5)
        cookie_result = await dismiss_cookie_banner(page)
        if not cookie_result.dismissed:
            await asyncio.sleep(2.0)
            cookie_result = await dismiss_cookie_banner(page)

        session._navigating_pages.discard(page_id)

        await event_emitter.emit_navigation_finished(page.url, page_id)

        summary = await _build_page_summary(page)
        if cookie_result.dismissed:
            summary["cookie_banner_dismissed"] = True

        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary=summary,
        )
    except Exception as e:
        session._navigating_pages.discard(page_id)
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("back")
async def handle_back(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Go back in history."""
    session_id = request.args.get("session", "default")

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        await page.go_back()
        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary={"url": page.url},
        )
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("forward")
async def handle_forward(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Go forward in history."""
    session_id = request.args.get("session", "default")

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        await page.go_forward()
        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary={"url": page.url},
        )
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("reload")
async def handle_reload(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Reload the current page."""
    session_id = request.args.get("session", "default")

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        await page.reload()
        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary={"url": page.url},
        )
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))
