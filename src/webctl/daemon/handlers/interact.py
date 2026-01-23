"""
Interaction command handlers (click, type, scroll, etc.).
"""

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, TypeVar, cast

from playwright.async_api import Page

from ...exceptions import AmbiguousTargetError, NoMatchError, ParseError
from ...protocol.messages import DoneResponse, ErrorResponse, Request, Response
from ...query.parser import parse_query
from ...query.resolver import QueryResolver
from ..session_manager import SessionManager
from .error_screenshot import capture_error_screenshot
from .registry import register

T = TypeVar("T")


async def with_retry(
    coro_fn: Callable[[], Coroutine[Any, Any, T]],
    retries: int,
    delay_ms: int,
) -> T:
    """Execute coroutine with retries."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await coro_fn()
        except Exception as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep(delay_ms / 1000)
    if last_error:
        raise last_error
    raise RuntimeError("Retry failed with no error")


# Aria role type from Playwright
AriaRole = str  # Playwright uses Literal type, we use str for flexibility


@dataclass
class ResolveError:
    """Detailed error info from element resolution."""

    code: str
    message: str
    suggestions: list[str]
    similar_elements: list[dict[str, Any]] | None = None


@dataclass
class ResolveSuccess:
    """Successful resolution result."""

    element: dict[str, Any]
    all_matches: list[dict[str, Any]] | None = None


async def resolve_element_detailed(
    page: Page, query_str: str, strict: bool = True
) -> ResolveSuccess | ResolveError:
    """Resolve a query to a single element with detailed error info."""
    from ...views.a11y import parse_aria_snapshot

    # Get snapshot
    try:
        snapshot_str = await page.locator("body").aria_snapshot()
    except Exception as e:
        return ResolveError(
            code="snapshot_failed",
            message=f"Failed to get page snapshot: {e}",
            suggestions=["Try waiting for the page to load", "Check if the page is accessible"],
        )

    if not snapshot_str:
        return ResolveError(
            code="empty_snapshot",
            message="Page snapshot is empty",
            suggestions=[
                "The page may still be loading",
                "Try 'webctl snapshot' to see page content",
            ],
        )

    # Parse snapshot
    items = parse_aria_snapshot(snapshot_str)

    # Collect available roles and names for suggestions
    available_roles: set[str] = set()
    available_names: list[str] = []
    for item in items:
        if item.get("role"):
            available_roles.add(item["role"])
        if item.get("name"):
            available_names.append(item["name"])

    # Parse query
    try:
        query = parse_query(query_str)
    except ParseError as e:
        return ResolveError(
            code="parse_error",
            message=f"Invalid query syntax: {e}",
            suggestions=[
                'Query format: role=button name~="text"',
                "Use name~= for partial match, name= for exact match",
                f"Available roles on page: {', '.join(sorted(available_roles)[:8])}",
            ],
        )

    # Extract role/name from query for better suggestions
    query_role = None
    query_name = None
    role_match = re.search(r"role=(\w+)", query_str)
    if role_match:
        query_role = role_match.group(1).lower()
    name_match = re.search(r'name[~]?=["\']?([^"\']+)["\']?', query_str)
    if name_match:
        query_name = name_match.group(1)

    # Create tree and resolve
    tree: dict[str, Any] = {"role": "root", "children": items}
    resolver = QueryResolver(tree, strict=strict)

    try:
        result = resolver.resolve(query)
        if result.count > 0:
            return ResolveSuccess(
                element=result.matches[0],
                all_matches=result.matches if result.count > 1 else None,
            )
    except NoMatchError:
        suggestions = []
        similar_elements = []

        # Role suggestion
        if query_role and query_role not in available_roles:
            similar_roles = get_close_matches(query_role, list(available_roles), n=3, cutoff=0.6)
            if similar_roles:
                suggestions.append(
                    f"Role '{query_role}' not found. Did you mean: {', '.join(similar_roles)}?"
                )
            else:
                suggestions.append(
                    f"Role '{query_role}' not found. Available: {', '.join(sorted(available_roles)[:8])}"
                )

        # Name suggestion
        if query_name:
            similar_names = get_close_matches(query_name, available_names, n=3, cutoff=0.4)
            if similar_names:
                suggestions.append(f"No match for '{query_name}'. Similar: {similar_names}")
                # Find elements with similar names
                for item in items:
                    if item.get("name") in similar_names:
                        similar_elements.append(
                            {
                                "id": item.get("id"),
                                "role": item.get("role"),
                                "name": item.get("name"),
                            }
                        )
            suggestions.append('Try name~="pattern" for partial matching')

        if not suggestions:
            suggestions.append("Use 'webctl query \"your query\"' to debug")
            suggestions.append("Use 'webctl snapshot --interactive-only' to see available elements")

        return ResolveError(
            code="no_match",
            message=f"No element matches query: {query_str}",
            suggestions=suggestions,
            similar_elements=similar_elements if similar_elements else None,
        )

    except AmbiguousTargetError as e:
        matches_info = [
            {"id": m.get("id"), "role": m.get("role"), "name": m.get("name", "")[:50]}
            for m in e.matches[:5]
        ]
        return ResolveError(
            code="ambiguous",
            message=f"Query matched {len(e.matches)} elements (expected 1)",
            suggestions=[
                'Add more filters to narrow down: role=X name~="specific text"',
                "Use nth(0) to select first match",
                f"Matches: {matches_info}",
            ],
            similar_elements=matches_info,
        )

    except Exception as e:
        return ResolveError(
            code="resolve_error",
            message=f"Query resolution failed: {e}",
            suggestions=["Check query syntax", "Try 'webctl query \"your query\"' to debug"],
        )

    return ResolveError(
        code="unknown",
        message="Element not found",
        suggestions=["Use 'webctl snapshot' to see available elements"],
    )


async def resolve_element(page: Page, query_str: str, strict: bool = True) -> dict[str, Any] | None:
    """Resolve a query to a single element (simple API for backward compat)."""
    result = await resolve_element_detailed(page, query_str, strict)
    if isinstance(result, ResolveSuccess):
        return result.element
    return None


@register("click")
async def handle_click(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Click an element."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")
    retry = request.args.get("retry", 0)
    retry_delay = request.args.get("retry_delay", 1000)
    wait_after = request.args.get("wait_after")

    if not query:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'query' argument",
            code="missing_argument",
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
        result = await resolve_element_detailed(page, query)
        if isinstance(result, ResolveError):
            yield ErrorResponse(
                req_id=request.req_id,
                error=result.message,
                code=result.code,
                details={
                    "suggestions": result.suggestions,
                    "similar_elements": result.similar_elements,
                },
            )
            return

        element = result.element

        # Get role and name to find element via Playwright
        role = element.get("role")
        name = element.get("name")

        locator = (
            page.get_by_role(cast(Any, role), name=name)
            if name
            else page.get_by_role(cast(Any, role))
        )

        # Click with retry support
        async def do_click() -> None:
            await locator.first.click()

        await with_retry(do_click, retry, retry_delay)

        summary: dict[str, Any] = {"clicked": {"role": role, "name": name}}
        if wait_after:
            from .wait import perform_wait

            await perform_wait(page, wait_after, timeout=30000)
            summary["waited_for"] = wait_after

        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary=summary,
        )

    except Exception as e:
        screenshot_path = await capture_error_screenshot(page, "click", "click_failed")
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Click failed: {e}",
            code="click_failed",
            details={"query": query, "screenshot": screenshot_path},
        )


@register("type")
async def handle_type(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Type text into an element."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")
    text = request.args.get("text", "")
    clear = request.args.get("clear", False)
    submit = request.args.get("submit", False)
    retry = request.args.get("retry", 0)
    retry_delay = request.args.get("retry_delay", 1000)
    wait_after = request.args.get("wait_after")

    if not query:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'query' argument",
            code="missing_argument",
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
        result = await resolve_element_detailed(page, query)
        if isinstance(result, ResolveError):
            yield ErrorResponse(
                req_id=request.req_id,
                error=result.message,
                code=result.code,
                details={
                    "suggestions": result.suggestions,
                    "similar_elements": result.similar_elements,
                },
            )
            return

        element = result.element
        role = element.get("role")
        name = element.get("name")

        locator = (
            page.get_by_role(cast(Any, role), name=name)
            if name
            else page.get_by_role(cast(Any, role))
        )

        # Type with retry support
        async def do_type() -> None:
            if clear:
                await locator.first.clear()
            await locator.first.fill(text)
            if submit:
                await locator.first.press("Enter")

        await with_retry(do_type, retry, retry_delay)

        summary: dict[str, Any] = {"typed": {"role": role, "name": name, "text_length": len(text)}}
        if wait_after:
            from .wait import perform_wait

            await perform_wait(page, wait_after, timeout=30000)
            summary["waited_for"] = wait_after

        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary=summary,
        )

    except Exception as e:
        screenshot_path = await capture_error_screenshot(page, "type", "type_failed")
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Type failed: {e}",
            code="type_failed",
            details={"query": query, "screenshot": screenshot_path},
        )


@register("set-value")
async def handle_set_value(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Set value of an input element."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")
    value = request.args.get("value", "")

    if not query:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'query' argument",
            code="missing_argument",
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
        result = await resolve_element_detailed(page, query)
        if isinstance(result, ResolveError):
            yield ErrorResponse(
                req_id=request.req_id,
                error=result.message,
                code=result.code,
                details={
                    "suggestions": result.suggestions,
                    "similar_elements": result.similar_elements,
                },
            )
            return

        element = result.element
        role = element.get("role")
        name = element.get("name")

        locator = (
            page.get_by_role(cast(Any, role), name=name)
            if name
            else page.get_by_role(cast(Any, role))
        )

        await locator.first.fill(value)

        yield DoneResponse(req_id=request.req_id, ok=True)

    except Exception as e:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Set value failed: {e}",
            code="set_value_failed",
            details={"query": query},
        )


@register("scroll")
async def handle_scroll(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Scroll the page or an element."""
    session_id = request.args.get("session", "default")
    direction = request.args.get("direction", "down")
    amount = request.args.get("amount", 300)
    query = request.args.get("query")

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        if query:
            # Scroll element into view
            result = await resolve_element_detailed(page, query)
            if isinstance(result, ResolveError):
                yield ErrorResponse(
                    req_id=request.req_id,
                    error=result.message,
                    code=result.code,
                    details={
                        "suggestions": result.suggestions,
                        "similar_elements": result.similar_elements,
                    },
                )
                return

            element = result.element
            role = element.get("role")
            name = element.get("name")

            locator = (
                page.get_by_role(cast(Any, role), name=name)
                if name
                else page.get_by_role(cast(Any, role))
            )

            await locator.first.scroll_into_view_if_needed()
        else:
            # Scroll the page
            delta_y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, delta_y)

        yield DoneResponse(req_id=request.req_id, ok=True)

    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("press")
async def handle_press(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Press a key."""
    session_id = request.args.get("session", "default")
    key = request.args.get("key")

    if not key:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'key' argument",
            code="missing_argument",
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
        await page.keyboard.press(key)
        yield DoneResponse(req_id=request.req_id, ok=True)

    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("select")
async def handle_select(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Select an option in a dropdown/select element."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")
    value = request.args.get("value")
    label = request.args.get("label")

    if not query:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'query' argument",
            code="missing_argument",
        )
        return

    if not value and not label:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'value' or 'label' argument",
            code="missing_argument",
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
        result = await resolve_element_detailed(page, query)
        if isinstance(result, ResolveError):
            yield ErrorResponse(
                req_id=request.req_id,
                error=result.message,
                code=result.code,
                details={
                    "suggestions": result.suggestions,
                    "similar_elements": result.similar_elements,
                },
            )
            return

        element = result.element
        role = element.get("role")
        name = element.get("name")

        locator = (
            page.get_by_role(cast(Any, role), name=name)
            if name
            else page.get_by_role(cast(Any, role))
        )

        if value:
            await locator.first.select_option(value=value)
        else:
            await locator.first.select_option(label=label)

        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary={"selected": value or label},
        )

    except Exception as e:
        screenshot_path = await capture_error_screenshot(page, "select", "select_failed")
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Select failed: {e}",
            code="select_failed",
            details={"query": query, "screenshot": screenshot_path},
        )


@register("check")
async def handle_check(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Check a checkbox or radio button."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")

    if not query:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'query' argument",
            code="missing_argument",
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
        result = await resolve_element_detailed(page, query)
        if isinstance(result, ResolveError):
            yield ErrorResponse(
                req_id=request.req_id,
                error=result.message,
                code=result.code,
                details={
                    "suggestions": result.suggestions,
                    "similar_elements": result.similar_elements,
                },
            )
            return

        element = result.element
        role = element.get("role")
        name = element.get("name")

        locator = (
            page.get_by_role(cast(Any, role), name=name)
            if name
            else page.get_by_role(cast(Any, role))
        )

        await locator.first.check()

        yield DoneResponse(req_id=request.req_id, ok=True, summary={"checked": True})

    except Exception as e:
        screenshot_path = await capture_error_screenshot(page, "check", "check_failed")
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Check failed: {e}",
            code="check_failed",
            details={"query": query, "screenshot": screenshot_path},
        )


@register("uncheck")
async def handle_uncheck(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Uncheck a checkbox."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")

    if not query:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'query' argument",
            code="missing_argument",
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
        result = await resolve_element_detailed(page, query)
        if isinstance(result, ResolveError):
            yield ErrorResponse(
                req_id=request.req_id,
                error=result.message,
                code=result.code,
                details={
                    "suggestions": result.suggestions,
                    "similar_elements": result.similar_elements,
                },
            )
            return

        element = result.element
        role = element.get("role")
        name = element.get("name")

        locator = (
            page.get_by_role(cast(Any, role), name=name)
            if name
            else page.get_by_role(cast(Any, role))
        )

        await locator.first.uncheck()

        yield DoneResponse(req_id=request.req_id, ok=True, summary={"checked": False})

    except Exception as e:
        screenshot_path = await capture_error_screenshot(page, "uncheck", "uncheck_failed")
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Uncheck failed: {e}",
            code="uncheck_failed",
            details={"query": query, "screenshot": screenshot_path},
        )


@register("upload")
async def handle_upload(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Upload a file to a file input element."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")
    file_path = request.args.get("file")

    if not query:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'query' argument",
            code="missing_argument",
        )
        return

    if not file_path:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'file' argument",
            code="missing_argument",
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
        # For file inputs, we can use set_input_files directly on the locator
        result = await resolve_element_detailed(page, query)
        if isinstance(result, ResolveError):
            yield ErrorResponse(
                req_id=request.req_id,
                error=result.message,
                code=result.code,
                details={
                    "suggestions": result.suggestions,
                    "similar_elements": result.similar_elements,
                },
            )
            return

        element = result.element
        role = element.get("role")
        name = element.get("name")

        locator = (
            page.get_by_role(cast(Any, role), name=name)
            if name
            else page.get_by_role(cast(Any, role))
        )

        await locator.first.set_input_files(file_path)

        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary={"uploaded": file_path},
        )

    except Exception as e:
        screenshot_path = await capture_error_screenshot(page, "upload", "upload_failed")
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Upload failed: {e}",
            code="upload_failed",
            details={"query": query, "file": file_path, "screenshot": screenshot_path},
        )


@register("fill-form")
async def handle_fill_form(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Fill multiple form fields at once."""
    session_id = request.args.get("session", "default")
    fields = request.args.get("fields", {})

    if not fields:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No fields provided",
            code="missing_argument",
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

    results: list[dict[str, Any]] = []

    for field_name, value in fields.items():
        try:
            if isinstance(value, bool):
                try:
                    locator = page.get_by_role("checkbox", name=field_name)
                    if value:
                        await locator.first.check()
                    else:
                        await locator.first.uncheck()
                    results.append({"field": field_name, "ok": True, "action": "checkbox"})
                except Exception:
                    # Try as a label click
                    locator = page.get_by_label(field_name)
                    if value:
                        await locator.first.check()
                    else:
                        await locator.first.uncheck()
                    results.append({"field": field_name, "ok": True, "action": "checkbox"})

            elif isinstance(value, str):
                filled = False

                # Strategy 1: Try by role=textbox with name
                try:
                    locator = page.get_by_role("textbox", name=field_name)
                    await locator.first.fill(value)
                    filled = True
                except Exception:
                    pass

                # Strategy 2: Try by label
                if not filled:
                    try:
                        locator = page.get_by_label(field_name)
                        await locator.first.fill(value)
                        filled = True
                    except Exception:
                        pass

                # Strategy 3: Try by placeholder
                if not filled:
                    try:
                        locator = page.get_by_placeholder(field_name)
                        await locator.first.fill(value)
                        filled = True
                    except Exception:
                        pass

                if filled:
                    results.append({"field": field_name, "ok": True, "action": "fill"})
                else:
                    results.append(
                        {
                            "field": field_name,
                            "ok": False,
                            "error": f"Could not find field: {field_name}",
                        }
                    )

            else:
                results.append(
                    {
                        "field": field_name,
                        "ok": False,
                        "error": f"Unsupported value type: {type(value).__name__}",
                    }
                )

        except Exception as e:
            results.append({"field": field_name, "ok": False, "error": str(e)})

    success_count = sum(1 for r in results if r.get("ok"))
    total_count = len(results)

    yield DoneResponse(
        req_id=request.req_id,
        ok=success_count == total_count,
        summary={
            "filled": success_count,
            "total": total_count,
            "results": results,
        },
    )
