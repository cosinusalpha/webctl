"""
RFC SS12.5: Wait commands

Conditions:
- network-idle
- view-changed:a11y
- exists:<query>
- text-contains:"..."
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ...protocol.messages import DoneResponse, ErrorResponse, Request, Response
from ...query.parser import parse_query
from ...query.resolver import QueryResolver
from ...views.a11y import get_a11y_snapshot_hash
from ..detectors.view_change import wait_for_view_change
from ..session_manager import SessionManager
from .registry import register


@register("wait")
async def handle_wait(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Wait for a condition to be met."""
    until = request.args.get("until")
    timeout_ms = request.args.get("timeout", 30000)
    session_id = request.args.get("session", "default")

    if not until:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'until' argument",
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
        if until == "network-idle":
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)

        elif until.startswith("view-changed:"):
            # RFC: wait --until view-changed:a11y
            view = until.split(":")[1]  # "a11y", "md", etc.
            if view == "a11y":
                success = await wait_for_view_change(page, timeout_ms)
                if not success:
                    yield ErrorResponse(
                        req_id=request.req_id,
                        error="Timeout waiting for view change",
                        code="timeout",
                    )
                    return

        elif until.startswith("exists:"):
            # Wait for element matching query to exist
            query_str = until[7:]  # Strip "exists:" prefix
            query = parse_query(query_str)

            async def element_exists() -> bool:
                try:
                    snapshot_str = await page.locator("body").aria_snapshot()
                    if not snapshot_str:
                        return False
                    # Parse the snapshot and build a tree for the resolver
                    from ...views.a11y import parse_aria_snapshot

                    items = parse_aria_snapshot(snapshot_str)
                    # Create a simple tree structure for the resolver
                    tree: dict[str, Any] = {"role": "root", "children": items}
                    resolver = QueryResolver(tree, strict=False)
                    result = resolver.resolve(query)
                    return result.count > 0
                except Exception:
                    return False

            success = await _poll_until(element_exists, timeout_ms)
            if not success:
                yield ErrorResponse(
                    req_id=request.req_id,
                    error=f"Timeout waiting for element: {query_str}",
                    code="timeout",
                )
                return

        elif until.startswith("text-contains:"):
            # Wait for text to appear on page
            text = until[14:]  # Strip "text-contains:" prefix
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]  # Remove quotes

            await page.wait_for_selector(f"text={text}", timeout=timeout_ms)

        elif until == "stable":
            # Wait for page to be stable (no pending requests, DOM stable)
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            # Additional stability check - wait for a11y tree to stabilize
            last_hash = ""
            stable_count = 0
            while stable_count < 3:  # Require 3 consecutive stable readings
                await asyncio.sleep(0.2)
                current_hash = await get_a11y_snapshot_hash(page)
                if current_hash == last_hash:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_hash = current_hash

        elif until == "load":
            await page.wait_for_load_state("load", timeout=timeout_ms)

        elif until == "domcontentloaded":
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

        elif until.startswith("visible:"):
            # Wait for element to be visible
            query_str = until[8:]
            query = parse_query(query_str)

            async def element_visible() -> bool:
                try:
                    snapshot_str = await page.locator("body").aria_snapshot()
                    if not snapshot_str:
                        return False
                    from ...views.a11y import parse_aria_snapshot

                    items = parse_aria_snapshot(snapshot_str)
                    tree: dict[str, Any] = {"role": "root", "children": items}
                    resolver = QueryResolver(tree, strict=False)
                    result = resolver.resolve(query)
                    if result.count == 0:
                        return False
                    element = result.matches[0]
                    role = element.get("role")
                    name = element.get("name")
                    if role is None:
                        return False
                    locator = page.get_by_role(role, name=name) if name else page.get_by_role(role)
                    return await locator.first.is_visible()
                except Exception:
                    return False

            success = await _poll_until(element_visible, timeout_ms)
            if not success:
                yield ErrorResponse(
                    req_id=request.req_id,
                    error=f"Timeout waiting for visible element: {query_str}",
                    code="timeout",
                )
                return

        elif until.startswith("hidden:"):
            # Wait for element to be hidden/removed
            query_str = until[7:]
            query = parse_query(query_str)

            async def element_hidden() -> bool:
                try:
                    snapshot_str = await page.locator("body").aria_snapshot()
                    if not snapshot_str:
                        return True
                    from ...views.a11y import parse_aria_snapshot

                    items = parse_aria_snapshot(snapshot_str)
                    tree: dict[str, Any] = {"role": "root", "children": items}
                    resolver = QueryResolver(tree, strict=False)
                    result = resolver.resolve(query)
                    return result.count == 0
                except Exception:
                    return True

            success = await _poll_until(element_hidden, timeout_ms)
            if not success:
                yield ErrorResponse(
                    req_id=request.req_id,
                    error=f"Timeout waiting for element to hide: {query_str}",
                    code="timeout",
                )
                return

        elif until.startswith("url-contains:"):
            # Wait for URL to contain substring
            text = until[13:]
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]

            async def url_contains() -> bool:
                return text in page.url

            success = await _poll_until(url_contains, timeout_ms)
            if not success:
                yield ErrorResponse(
                    req_id=request.req_id,
                    error=f"Timeout waiting for URL to contain: {text}",
                    code="timeout",
                )
                return

        elif until.startswith("enabled:"):
            # Wait for element to be enabled (not disabled)
            query_str = until[8:]
            query = parse_query(query_str)

            async def element_enabled() -> bool:
                try:
                    snapshot_str = await page.locator("body").aria_snapshot()
                    if not snapshot_str:
                        return False
                    from ...views.a11y import parse_aria_snapshot

                    items = parse_aria_snapshot(snapshot_str)
                    tree: dict[str, Any] = {"role": "root", "children": items}
                    resolver = QueryResolver(tree, strict=False)
                    result = resolver.resolve(query)
                    if result.count == 0:
                        return False
                    element = result.matches[0]
                    return not element.get("disabled", False)
                except Exception:
                    return False

            success = await _poll_until(element_enabled, timeout_ms)
            if not success:
                yield ErrorResponse(
                    req_id=request.req_id,
                    error=f"Timeout waiting for enabled element: {query_str}",
                    code="timeout",
                )
                return

        else:
            yield ErrorResponse(
                req_id=request.req_id,
                error=f"Unknown wait condition: {until}",
                code="invalid_condition",
                details={
                    "available_conditions": [
                        "network-idle",
                        "load",
                        "domcontentloaded",
                        "stable",
                        "exists:<query>",
                        "visible:<query>",
                        "hidden:<query>",
                        "enabled:<query>",
                        'text-contains:"text"',
                        'url-contains:"text"',
                        "view-changed:a11y",
                    ]
                },
            )
            return

        yield DoneResponse(req_id=request.req_id, ok=True)

    except TimeoutError:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Timeout waiting for: {until}",
            code="timeout",
        )
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


async def _poll_until(
    condition: Callable[[], Awaitable[bool]], timeout_ms: int, interval_ms: int = 200
) -> bool:
    """Poll until condition returns True or timeout."""
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)

    while asyncio.get_event_loop().time() < deadline:
        if await condition():
            return True
        await asyncio.sleep(interval_ms / 1000)

    return False


async def perform_wait(page: Any, until: str, timeout: int = 30000) -> None:
    """
    Perform a wait operation on a page.

    This is a reusable function for wait operations that can be called
    from other handlers (e.g., click --wait).

    Args:
        page: Playwright page object
        until: Wait condition string
        timeout: Timeout in milliseconds

    Raises:
        TimeoutError: If wait times out
        ValueError: If condition is invalid
    """
    if until == "network-idle":
        await page.wait_for_load_state("networkidle", timeout=timeout)

    elif until == "load":
        await page.wait_for_load_state("load", timeout=timeout)

    elif until == "domcontentloaded":
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)

    elif until == "stable":
        await page.wait_for_load_state("networkidle", timeout=timeout)
        last_hash = ""
        stable_count = 0
        while stable_count < 3:
            await asyncio.sleep(0.2)
            current_hash = await get_a11y_snapshot_hash(page)
            if current_hash == last_hash:
                stable_count += 1
            else:
                stable_count = 0
                last_hash = current_hash

    elif until.startswith("exists:"):
        query_str = until[7:]
        query = parse_query(query_str)

        async def element_exists() -> bool:
            try:
                snapshot_str = await page.locator("body").aria_snapshot()
                if not snapshot_str:
                    return False
                from ...views.a11y import parse_aria_snapshot

                items = parse_aria_snapshot(snapshot_str)
                tree: dict[str, Any] = {"role": "root", "children": items}
                resolver = QueryResolver(tree, strict=False)
                result = resolver.resolve(query)
                return result.count > 0
            except Exception:
                return False

        success = await _poll_until(element_exists, timeout)
        if not success:
            raise TimeoutError(f"Timeout waiting for element: {query_str}")

    elif until.startswith("url-contains:"):
        text = until[13:]
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]

        async def url_contains() -> bool:
            return text in page.url

        success = await _poll_until(url_contains, timeout)
        if not success:
            raise TimeoutError(f"Timeout waiting for URL to contain: {text}")

    elif until.startswith("text-contains:"):
        text = until[14:]
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        await page.wait_for_selector(f"text={text}", timeout=timeout)

    else:
        raise ValueError(f"Unknown wait condition: {until}")
