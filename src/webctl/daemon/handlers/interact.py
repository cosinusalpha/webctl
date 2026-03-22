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
from ...protocol.messages import DoneResponse, ErrorResponse, ItemResponse, Request, Response
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


def resolve_ref_to_query(session_state: Any, query_str: str) -> str:
    """If query_str is a @ref (e.g. '@e1'), resolve it to a role/name query. Otherwise pass through."""
    if not query_str.startswith("@"):
        return query_str
    ref_data = session_state.resolve_ref(query_str)
    if ref_data is None:
        return query_str  # Let it fail downstream with a clear error
    role = ref_data.get("role", "")
    name = ref_data.get("name", "")
    if name:
        escaped = name.replace('"', '\\"')
        return f'role={role} name~="{escaped}"'
    return f"role={role}"


# --- Role categories for implicit resolution ---
CLICKABLE_ROLES = frozenset({"button", "link", "menuitem", "tab", "option", "treeitem", "switch"})
TYPEABLE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "listbox"})
CHECKABLE_ROLES = frozenset({"checkbox", "radio", "switch"})
from ...views.filters import INTERACTIVE_ROLES


def _is_query_syntax(target: str) -> bool:
    """Check if target looks like query syntax (role=X, name=Y, etc.)."""
    return bool(re.search(r"\b(role|name|text|id)([~]?=)", target))


async def resolve_by_description(
    page: Page,
    description: str,
    preferred_roles: frozenset[str] | None = None,
) -> ResolveSuccess | ResolveError:
    """Resolve a plain text description to an interactive element via fuzzy matching."""
    from ...views.a11y import parse_aria_snapshot

    try:
        snapshot_str = await page.locator("body").aria_snapshot()
    except Exception as e:
        return ResolveError(
            code="snapshot_failed",
            message=f"Failed to get page snapshot: {e}",
            suggestions=["Try waiting for the page to load"],
        )

    if not snapshot_str:
        return ResolveError(
            code="empty_snapshot",
            message="Page snapshot is empty",
            suggestions=["The page may still be loading"],
        )

    items = parse_aria_snapshot(snapshot_str)
    interactive = [i for i in items if i.get("role") in INTERACTIVE_ROLES]
    lower_desc = description.lower()

    # 1. Exact name match (case-insensitive)
    exact = [i for i in interactive if i.get("name", "").lower() == lower_desc]
    if len(exact) == 1:
        return ResolveSuccess(element=exact[0])

    # 2. Substring match
    substring = [i for i in interactive if lower_desc in i.get("name", "").lower()]

    # Prefer matches in preferred roles
    if preferred_roles and len(substring) > 1:
        preferred = [i for i in substring if i.get("role") in preferred_roles]
        if len(preferred) == 1:
            return ResolveSuccess(element=preferred[0])
        if preferred:
            substring = preferred

    if len(substring) == 1:
        return ResolveSuccess(element=substring[0])

    if len(substring) > 1:
        candidates = [
            {"ref": f"@e{idx}", "role": m.get("role"), "name": m.get("name", "")[:60]}
            for idx, m in enumerate(substring[:5], 1)
        ]
        return ResolveError(
            code="ambiguous",
            message=f"'{description}' matched {len(substring)} elements. Be more specific or use @refs from a snapshot.",
            suggestions=[f"Candidates: {candidates}"],
            similar_elements=candidates,
        )

    # 3. Fuzzy match
    all_names = [i.get("name", "") for i in interactive if i.get("name")]
    similar = get_close_matches(description, all_names, n=5, cutoff=0.4)
    if similar:
        similar_elements = [
            {"role": i.get("role"), "name": i.get("name")}
            for i in interactive
            if i.get("name") in similar
        ][:5]
        return ResolveError(
            code="no_match",
            message=f"No element matching '{description}'",
            suggestions=[f"Did you mean: {', '.join(similar)}"],
            similar_elements=similar_elements,
        )

    return ResolveError(
        code="no_match",
        message=f"No interactive element matching '{description}'",
        suggestions=["Run 'webctl snapshot' to see available elements"],
    )


async def resolve_target(
    page: Page,
    session: Any | None,
    target: str,
    preferred_roles: frozenset[str] | None = None,
) -> ResolveSuccess | ResolveError:
    """Smart element resolution: @ref, query syntax, or text description."""
    # 1. @ref
    if target.startswith("@") and session:
        ref_data = session.resolve_ref(target)
        if ref_data:
            role = ref_data.get("role", "")
            name = ref_data.get("name", "")
            if name:
                escaped = name.replace('"', '\\"')
                query_str = f'role={role} name~="{escaped}"'
            else:
                query_str = f"role={role}"
            return await resolve_element_detailed(page, query_str)
        return ResolveError(
            code="ref_not_found",
            message=f"Reference {target} not found. Run 'webctl snapshot' to get current refs.",
            suggestions=["Take a new snapshot to get updated @refs"],
        )

    # 2. Query syntax (contains role=, name=, etc.)
    if _is_query_syntax(target):
        return await resolve_element_detailed(page, target)

    # 3. Plain text description -> fuzzy match
    return await resolve_by_description(page, target, preferred_roles)


async def resolve_with_fallbacks(
    page: Page,
    session: Any | None,
    target: str,
    preferred_roles: frozenset[str] | None = None,
    max_scrolls: int = 2,
) -> ResolveSuccess | ResolveError:
    """Resolve target with automatic fallbacks: overlay dismiss + scroll-to-find."""
    from ..detectors.cookie_banner import dismiss_cookie_banner

    result = await resolve_target(page, session, target, preferred_roles)
    if isinstance(result, ResolveSuccess):
        return result

    # Fallback 1: Dismiss overlays and retry
    if result.code in ("no_match",):
        try:
            cookie_result = await dismiss_cookie_banner(page)
            if cookie_result.dismissed:
                result = await resolve_target(page, session, target, preferred_roles)
                if isinstance(result, ResolveSuccess):
                    return result
        except Exception:
            pass

    # Fallback 2: Scroll down and retry (element may be below fold)
    if result.code == "no_match":
        for _ in range(max_scrolls):
            await page.mouse.wheel(0, 600)
            await asyncio.sleep(0.5)
            result = await resolve_target(page, session, target, preferred_roles)
            if isinstance(result, ResolveSuccess):
                return result

    return result


def make_locator(page: Page, element: dict[str, Any]) -> Any:
    """Build a Playwright locator from a resolved element."""
    role = element.get("role")
    name = element.get("name")
    return (
        page.get_by_role(cast(Any, role), name=name)
        if name
        else page.get_by_role(cast(Any, role))
    )


async def _click_with_overlay_retry(locator: Any, page: Page) -> None:
    """Click with automatic overlay dismiss retry on interception."""
    try:
        await locator.first.click()
    except Exception as e:
        err = str(e).lower()
        if "intercept" in err or "obscur" in err or "overlay" in err:
            from ..detectors.cookie_banner import dismiss_cookie_banner

            await dismiss_cookie_banner(page)
            await asyncio.sleep(0.3)
            await locator.first.click()
        else:
            raise


async def _snapshot_after(
    request: Request, session_manager: SessionManager, session_id: str
) -> AsyncIterator[Response]:
    """Take a compact ref snapshot and yield it as items. Used by --snapshot flag."""
    from ...views.a11y import A11yExtractOptions, extract_a11y_view

    page = session_manager.get_active_page(session_id)
    if not page:
        return

    options = A11yExtractOptions(
        include_path_hint=False,
        interactive_only=True,
        compact_refs=True,
    )
    collected: list[dict[str, Any]] = []
    async for item in extract_a11y_view(page, options):
        collected.append(item)

    # Store refs
    session = session_manager.get_session(session_id)
    if session:
        id_to_ref = session.store_refs(collected)
        for item in collected:
            item_id = item.get("id", "")
            if item_id in id_to_ref:
                item["ref"] = id_to_ref[item_id]

    for item in collected:
        item["req_id"] = request.req_id
        yield ItemResponse(req_id=request.req_id, view="a11y", data=item)


@register("click")
async def handle_click(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Click an element. Target can be @ref, query, or text description."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")
    retry = request.args.get("retry", 0)
    retry_delay = request.args.get("retry_delay", 1000)
    wait_after = request.args.get("wait_after")
    snapshot_after = request.args.get("snapshot_after", False)

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

    session = session_manager.get_session(session_id)

    try:
        result = await resolve_with_fallbacks(page, session, query, CLICKABLE_ROLES)
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
        locator = make_locator(page, element)

        async def do_click() -> None:
            await _click_with_overlay_retry(locator, page)

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

        if snapshot_after:
            async for resp in _snapshot_after(request, session_manager, session_id):
                yield resp

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
    """Type text into an element. Auto-detects combobox (select_option) and checkbox (check/uncheck)."""
    session_id = request.args.get("session", "default")
    query = request.args.get("query")
    text = request.args.get("text", "")
    clear = request.args.get("clear", False)
    submit = request.args.get("submit", False)
    retry = request.args.get("retry", 0)
    retry_delay = request.args.get("retry_delay", 1000)
    wait_after = request.args.get("wait_after")
    snapshot_after = request.args.get("snapshot_after", False)

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

    session = session_manager.get_session(session_id)

    try:
        result = await resolve_with_fallbacks(page, session, query, TYPEABLE_ROLES)
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
        locator = make_locator(page, element)

        # Smart type detection based on element role
        action_taken = "fill"

        async def do_type() -> None:
            nonlocal action_taken
            if role in ("combobox", "listbox"):
                # Auto-detect: select option by label instead of typing
                try:
                    await locator.first.select_option(label=text)
                    action_taken = "select_option"
                except Exception:
                    # Fallback: some comboboxes need click + type
                    await locator.first.click()
                    await page.keyboard.type(text)
                    action_taken = "keyboard_type"
            elif role in CHECKABLE_ROLES:
                # Auto-detect: check/uncheck based on text
                if text.lower() in ("true", "yes", "on", "1", "check"):
                    await locator.first.check()
                    action_taken = "check"
                else:
                    await locator.first.uncheck()
                    action_taken = "uncheck"
            else:
                # Standard textbox fill
                if clear:
                    await locator.first.clear()
                await locator.first.fill(text)
                if submit:
                    await locator.first.press("Enter")

        await with_retry(do_type, retry, retry_delay)

        summary: dict[str, Any] = {
            "typed": {"role": role, "name": name, "text_length": len(text), "action": action_taken}
        }
        if wait_after:
            from .wait import perform_wait

            await perform_wait(page, wait_after, timeout=30000)
            summary["waited_for"] = wait_after

        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary=summary,
        )

        if snapshot_after:
            async for resp in _snapshot_after(request, session_manager, session_id):
                yield resp

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
    snapshot_after = request.args.get("snapshot_after", False)

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
            session = session_manager.get_session(session_id)
            result = await resolve_target(page, session, query)
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

            locator = make_locator(page, result.element)
            await locator.first.scroll_into_view_if_needed()
        else:
            delta_y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, delta_y)

        yield DoneResponse(req_id=request.req_id, ok=True)

        if snapshot_after:
            async for resp in _snapshot_after(request, session_manager, session_id):
                yield resp

    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("press")
async def handle_press(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Press a key."""
    session_id = request.args.get("session", "default")
    key = request.args.get("key")
    snapshot_after = request.args.get("snapshot_after", False)

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

        if snapshot_after:
            async for resp in _snapshot_after(request, session_manager, session_id):
                yield resp

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

                try:
                    locator = page.get_by_role("textbox", name=field_name)
                    await locator.first.fill(value)
                    filled = True
                except Exception:
                    pass

                if not filled:
                    try:
                        locator = page.get_by_label(field_name)
                        await locator.first.fill(value)
                        filled = True
                    except Exception:
                        pass

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


@register("do")
async def handle_do(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Execute multiple actions sequentially in one call.

    Actions format: list of [action, target, value?] tuples.
    Example: [["type", "Email", "user@test.com"], ["type", "Password", "secret"], ["click", "Log in"]]
    """
    session_id = request.args.get("session", "default")
    actions = request.args.get("actions", [])
    snapshot_after = request.args.get("snapshot_after", False)

    if not actions:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No actions provided",
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

    session = session_manager.get_session(session_id)
    completed: list[dict[str, Any]] = []

    for idx, action_spec in enumerate(actions):
        if not isinstance(action_spec, list) or len(action_spec) < 2:
            yield ErrorResponse(
                req_id=request.req_id,
                error=f"Action {idx}: invalid format, expected [action, target, ...value]",
                code="invalid_action",
                details={"completed": completed, "failed_at": idx},
            )
            return

        action = action_spec[0]
        target = action_spec[1]
        value = action_spec[2] if len(action_spec) > 2 else None

        try:
            if action == "click":
                result = await resolve_with_fallbacks(page, session, target, CLICKABLE_ROLES)
                if isinstance(result, ResolveError):
                    yield ErrorResponse(
                        req_id=request.req_id,
                        error=f"Action {idx} (click '{target}'): {result.message}",
                        code=result.code,
                        details={
                            "suggestions": result.suggestions,
                            "similar_elements": result.similar_elements,
                            "completed": completed,
                            "failed_at": idx,
                        },
                    )
                    return
                locator = make_locator(page, result.element)
                await _click_with_overlay_retry(locator, page)
                completed.append({"action": "click", "target": target, "ok": True})

            elif action == "type":
                if value is None:
                    yield ErrorResponse(
                        req_id=request.req_id,
                        error=f"Action {idx} (type): missing value",
                        code="missing_argument",
                        details={"completed": completed, "failed_at": idx},
                    )
                    return
                result = await resolve_with_fallbacks(page, session, target, TYPEABLE_ROLES)
                if isinstance(result, ResolveError):
                    yield ErrorResponse(
                        req_id=request.req_id,
                        error=f"Action {idx} (type '{target}'): {result.message}",
                        code=result.code,
                        details={
                            "suggestions": result.suggestions,
                            "similar_elements": result.similar_elements,
                            "completed": completed,
                            "failed_at": idx,
                        },
                    )
                    return
                element = result.element
                role = element.get("role")
                locator = make_locator(page, element)

                if role in ("combobox", "listbox"):
                    try:
                        await locator.first.select_option(label=value)
                    except Exception:
                        await locator.first.click()
                        await page.keyboard.type(value)
                elif role in CHECKABLE_ROLES:
                    if value.lower() in ("true", "yes", "on", "1", "check"):
                        await locator.first.check()
                    else:
                        await locator.first.uncheck()
                else:
                    await locator.first.fill(value)
                completed.append({"action": "type", "target": target, "ok": True})

            elif action == "press":
                await page.keyboard.press(target)
                completed.append({"action": "press", "key": target, "ok": True})

            elif action == "scroll":
                direction = target.lower()
                delta_y = 300 if direction == "down" else -300
                await page.mouse.wheel(0, delta_y)
                completed.append({"action": "scroll", "direction": direction, "ok": True})

            elif action == "wait":
                from .wait import perform_wait

                await perform_wait(page, target, timeout=30000)
                completed.append({"action": "wait", "until": target, "ok": True})

            else:
                yield ErrorResponse(
                    req_id=request.req_id,
                    error=f"Action {idx}: unknown action '{action}'. Use: click, type, press, scroll, wait",
                    code="invalid_action",
                    details={"completed": completed, "failed_at": idx},
                )
                return

            # Brief stability pause between actions
            await asyncio.sleep(0.15)

        except Exception as e:
            yield ErrorResponse(
                req_id=request.req_id,
                error=f"Action {idx} ({action} '{target}'): {e}",
                code=f"{action}_failed",
                details={"completed": completed, "failed_at": idx},
            )
            return

    yield DoneResponse(
        req_id=request.req_id,
        ok=True,
        summary={"actions_completed": len(completed), "results": completed},
    )

    if snapshot_after:
        async for resp in _snapshot_after(request, session_manager, session_id):
            yield resp
