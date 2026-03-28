"""
Navigation command handlers.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

from ...protocol.messages import DoneResponse, ErrorResponse, ItemResponse, Request, Response
from ...views.a11y import parse_aria_snapshot
from ...views.filters import (
    NAVIGATE_ROLES,
    collapse_containers,
    deduplicate_adjacent,
    landmark_aware_filter,
)
from ..detectors.cookie_banner import dismiss_cookie_banner
from ..event_emitter import EventEmitter
from ..session_manager import SessionManager
from .registry import register

_NAVIGATE_ROLES_STR = ",".join(NAVIGATE_ROLES)


async def _build_smart_navigate_snapshot(
    page: "Page",  # noqa: F821
    session: Any,
    request: Request,
    max_name_length: int = 80,
    auto_limit: int = 200,
) -> tuple[list[Response], dict[str, Any]]:
    """Build a landmark-aware snapshot: dialog/alert first, main expanded, nav/footer collapsed."""
    from ...views.markdown import _extract_structured_data
    from ...views.redaction import redact_if_sensitive

    try:
        snapshot_str = await page.locator("body").aria_snapshot()
    except Exception:
        snapshot_str = ""

    if not snapshot_str:
        return [], {"total": 0, "by_role": {}}

    items = parse_aria_snapshot(snapshot_str)

    # Extract structured data (JSON-LD, Open Graph) — cheap and very useful for product/article pages
    structured_data = ""
    try:
        structured_data = await _extract_structured_data(page)
    except Exception:
        pass

    # Step 1: Landmark-aware partitioning (dialog first, main expanded, nav collapsed, etc.)
    items = landmark_aware_filter(items)

    # Step 2: Collapse combobox/listbox children
    items = collapse_containers(items)

    # Step 3: Deduplicate adjacent link+heading pairs with same name
    items = deduplicate_adjacent(items)

    # Step 4: Truncate names and redact sensitive content
    for item in items:
        name = item.get("name", "")
        if name:
            item["name"] = redact_if_sensitive(name, item.get("role") == "textbox" and "password" in name.lower())
            if max_name_length and len(item["name"]) > max_name_length:
                item["name"] = item["name"][:max_name_length - 3] + "..."

    # Step 4: Build stats
    stats: dict[str, Any] = {"total": len(items), "by_role": {}}
    for item in items:
        role = item.get("role", "unknown")
        stats["by_role"][role] = stats["by_role"].get(role, 0) + 1

    # Step 5: Auto-limit
    if len(items) > auto_limit:
        stats["total_before_limit"] = len(items)
        items = items[:auto_limit]
        stats["total"] = auto_limit
        stats["truncated"] = True
        stats["hint"] = "Use 'snapshot --grep \"pattern\"' or '--within \"role=main\"' to narrow scope"

    # Step 6: Remove internal _depth and assign refs
    for item in items:
        item.pop("_depth", None)
        # Ensure item has standard fields for output
        if "type" not in item:
            item["type"] = "item"
        if "view" not in item:
            item["view"] = "a11y"

    if session:
        id_to_ref = session.store_refs(items)
        for item in items:
            item_id = item.get("id", "")
            if item_id in id_to_ref:
                item["ref"] = id_to_ref[item_id]

    responses: list[Response] = []

    # Prepend structured data (JSON-LD/OG) as a text item if available
    if structured_data:
        responses.append(ItemResponse(
            req_id=request.req_id,
            view="md",
            data={"content": structured_data.strip(), "title": "", "url": ""},
        ))

    for item in items:
        responses.append(ItemResponse(req_id=request.req_id, view="a11y", data=item))

    return responses, stats


def _grep_filter_responses(
    responses: list[Response], pattern: str
) -> list[Response]:
    """Filter a11y ItemResponses by grep pattern on role+name. Keep non-a11y items (e.g. structured data)."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return responses

    filtered: list[Response] = []
    for resp in responses:
        if not isinstance(resp, ItemResponse) or resp.view != "a11y":
            # Keep structured data and other non-a11y items
            filtered.append(resp)
            continue
        data = resp.data
        text = f"{data.get('role', '')} {data.get('name', '')}"
        if regex.search(text):
            filtered.append(resp)
    return filtered



@register("navigate")
async def handle_navigate(
    request: Request,
    session_manager: SessionManager,
    event_emitter: EventEmitter,
    **kwargs: Any,
) -> AsyncIterator[Response]:
    """Navigate to a URL. Auto-starts session if needed. Returns structured data + a11y snapshot."""
    url = request.args.get("url")
    session_id = request.args.get("session", "default")
    wait_until = request.args.get("wait_until", "load")
    read_mode = request.args.get("read", False)
    search_query = request.args.get("search")
    grep_pattern = request.args.get("grep_pattern")

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
        # All pages closed — open a fresh one so navigate always works
        try:
            page = await session.context.new_page()
            await session_manager._register_page(session, page, "tab")
        except Exception as e:
            yield ErrorResponse(
                req_id=request.req_id,
                error=f"No active page and failed to open new one: {e}",
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

        # Navigate — domcontentloaded is fast, then custom idle fills the gap
        await page.goto(url, wait_until=wait_until)

        # Best-effort custom network idle (ignores media/websocket/eventsource)
        page_info = session_manager.get_active_page_info(session_id)
        if page_info and page_info.network_idle_detector:
            try:
                await page_info.network_idle_detector.wait(timeout_ms=5000)
            except (TimeoutError, Exception):
                pass  # Content is likely ready — proceed anyway

        # Auto-dismiss cookie banners (try twice — iframes may load late)
        await asyncio.sleep(1.5)
        cookie_result = await dismiss_cookie_banner(page)
        if not cookie_result.dismissed:
            await asyncio.sleep(2.0)
            cookie_result = await dismiss_cookie_banner(page)

        session._navigating_pages.discard(page_id)

        await event_emitter.emit_navigation_finished(page.url, page_id)

        # page.title() can fail if the page is mid-navigation (e.g. DDG client-side redirect)
        try:
            title = await page.title()
        except Exception:
            await asyncio.sleep(0.5)
            try:
                title = await page.title()
            except Exception:
                title = ""

        summary: dict[str, Any] = {
            "url": page.url,
            "title": title,
        }
        if cookie_result.dismissed:
            summary["cookie_banner_dismissed"] = True

        # --search: find search box, type query, press Enter, wait
        # Uses keyboard navigation like a screen reader — fill the box,
        # then press Enter via the keyboard (not the locator) because
        # comboboxes change their accessible name after fill.
        if search_query:
            from .interact import make_locator, resolve_searchbox

            search_result = await resolve_searchbox(page)
            if hasattr(search_result, "element"):
                locator = make_locator(page, search_result.element)
                await locator.first.fill(search_query)
                await page.keyboard.press("Enter")
                # Wait for search results to load
                if page_info and page_info.network_idle_detector:
                    try:
                        await page_info.network_idle_detector.wait(timeout_ms=10000)
                    except (TimeoutError, Exception):
                        await asyncio.sleep(2)
                else:
                    await asyncio.sleep(2)
                summary["searched"] = search_query
                summary["url"] = page.url
                try:
                    summary["title"] = await page.title()
                except Exception:
                    summary["title"] = ""
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

        # Default: full landmark-aware a11y snapshot (with structured data prepended)
        # --grep: same but filtered by pattern
        responses, snap_stats = await _build_smart_navigate_snapshot(
            page, session, request,
            max_name_length=80,
            auto_limit=200,
        )

        if grep_pattern:
            total_before = snap_stats.get("total", 0)
            responses = _grep_filter_responses(responses, grep_pattern)
            # Recount after filtering
            filtered_count = sum(
                1 for r in responses
                if isinstance(r, ItemResponse) and r.view == "a11y"
            )
            snap_stats = {
                "total": filtered_count,
                "grep": grep_pattern,
                "total_before_filter": total_before,
            }
            if filtered_count == 0 and total_before > 0:
                snap_stats["hint"] = "No a11y elements matched. Try: snapshot --read"

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
