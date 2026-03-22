"""
Navigation command handlers.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ...protocol.messages import DoneResponse, ErrorResponse, ItemResponse, Request, Response
from ...views.a11y import A11yExtractOptions, extract_a11y_view
from ...views.filters import NAVIGATE_ROLES
from ..detectors.cookie_banner import dismiss_cookie_banner
from ..event_emitter import EventEmitter
from ..session_manager import SessionManager
from .registry import register

_NAVIGATE_ROLES_STR = ",".join(NAVIGATE_ROLES)


async def _build_snapshot_with_refs(
    page: "Page",  # noqa: F821
    session: Any,
    request: Request,
    interactive_only: bool = False,
    roles: str | None = None,
    max_name_length: int | None = None,
    auto_limit: int | None = None,
) -> tuple[list[Response], dict[str, Any]]:
    """Build a snapshot with @refs, return (responses, stats)."""
    options = A11yExtractOptions(
        include_path_hint=False,
        interactive_only=interactive_only,
        compact_refs=True,
        roles=roles,
        max_name_length=max_name_length,
    )

    collected: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"total": 0, "by_role": {}}
    async for item in extract_a11y_view(page, options):
        stats["total"] += 1
        role = item.get("role", "unknown")
        stats["by_role"][role] = stats["by_role"].get(role, 0) + 1
        collected.append(item)

    # Auto-limit: truncate and hint if too many elements
    truncated = False
    if auto_limit and len(collected) > auto_limit:
        truncated = True
        stats["total_before_limit"] = len(collected)
        collected = collected[:auto_limit]
        stats["total"] = auto_limit
        stats["truncated"] = True
        stats["hint"] = "Use 'snapshot --grep \"pattern\"' or '--within \"role=main\"' to narrow scope"

    # Store refs in session
    if session:
        id_to_ref = session.store_refs(collected)
        for item in collected:
            item_id = item.get("id", "")
            if item_id in id_to_ref:
                item["ref"] = id_to_ref[item_id]

    responses: list[Response] = []
    for item in collected:
        responses.append(ItemResponse(req_id=request.req_id, view="a11y", data=item))

    return responses, stats


@register("navigate")
async def handle_navigate(
    request: Request,
    session_manager: SessionManager,
    event_emitter: EventEmitter,
    **kwargs: Any,
) -> AsyncIterator[Response]:
    """Navigate to a URL. Auto-starts session if needed. Returns snapshot with @refs."""
    url = request.args.get("url")
    session_id = request.args.get("session", "default")
    wait_until = request.args.get("wait_until", "load")
    read_mode = request.args.get("read", False)
    search_query = request.args.get("search")

    if not url:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'url' argument",
            code="missing_argument",
        )
        return

    # Auto-start session if needed
    try:
        session = await session_manager.ensure_session(session_id)
    except Exception as e:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Failed to start session: {e}",
            code="session_start_failed",
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

        summary: dict[str, Any] = {
            "url": page.url,
            "title": await page.title(),
        }
        if cookie_result.dismissed:
            summary["cookie_banner_dismissed"] = True

        # --search: find search box, type query, press Enter, wait
        if search_query:
            from .interact import TYPEABLE_ROLES, make_locator, resolve_by_description

            search_result = await resolve_by_description(
                page, "Search", frozenset({"searchbox", "textbox", "combobox"})
            )
            if hasattr(search_result, "element"):
                locator = make_locator(page, search_result.element)
                await locator.first.fill(search_query)
                await locator.first.press("Enter")
                # Wait for results
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    await asyncio.sleep(2)
                summary["searched"] = search_query
                summary["url"] = page.url
                summary["title"] = await page.title()
            else:
                summary["search_warning"] = "No search box found"

        # --read: return markdown content
        if read_mode:
            from ...views.markdown import extract_markdown_view

            md_items: list[str] = []
            async for item in extract_markdown_view(page):
                text = item.get("text", item.get("content", ""))
                if text:
                    md_items.append(text)

            if md_items:
                yield ItemResponse(
                    req_id=request.req_id,
                    view="md",
                    data={"text": "\n".join(md_items)},
                )

            yield DoneResponse(req_id=request.req_id, ok=True, summary=summary)
            return

        # Default: return snapshot with @refs (interactive + landmarks + key structural)
        responses, snap_stats = await _build_snapshot_with_refs(
            page, session, request,
            roles=_NAVIGATE_ROLES_STR,
            max_name_length=80,
            auto_limit=200,
        )
        summary["elements"] = snap_stats

        for resp in responses:
            yield resp

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
