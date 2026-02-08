"""
Observation command handlers (snapshot, screenshot, etc.).
"""

import base64
from collections.abc import AsyncIterator
from difflib import get_close_matches
from typing import Any

from ...exceptions import ParseError
from ...protocol.messages import DoneResponse, ErrorResponse, ItemResponse, Request, Response
from ...query.parser import parse_query
from ...views.a11y import A11yExtractOptions, extract_a11y_view, parse_aria_snapshot
from ...views.dom_lite import DomLiteOptions, extract_dom_lite_view
from ...views.filters import INTERACTIVE_ROLES, LANDMARK_ROLES, STRUCTURAL_ROLES
from ...views.markdown import extract_markdown_view
from ..session_manager import SessionManager
from .registry import register


@register("snapshot")
async def handle_snapshot(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Take a snapshot of the current page."""
    session_id = request.args.get("session", "default")
    view = request.args.get("view", "a11y")
    include_bbox = request.args.get("include_bbox", False)
    include_path_hint = request.args.get("include_path_hint", True)
    # Filtering options
    max_depth = request.args.get("max_depth")
    limit = request.args.get("limit")
    roles = request.args.get("roles")
    interactive_only = request.args.get("interactive_only", False)
    within = request.args.get("within")
    # Large output handling options
    grep_pattern = request.args.get("grep_pattern")
    max_name_length = request.args.get("max_name_length")
    visible_only = request.args.get(
        "visible_only", False
    )  # Default False - bbox check is expensive
    names_only = request.args.get("names_only", False)
    show_query = request.args.get("show_query", False)
    count_only = request.args.get("count_only", False)

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        if view == "a11y":
            options = A11yExtractOptions(
                include_bbox=include_bbox,
                include_path_hint=include_path_hint,
                max_depth=max_depth,
                limit=limit,
                roles=roles,
                interactive_only=interactive_only,
                within=within,
                grep_pattern=grep_pattern,
                max_name_length=max_name_length,
                visible_only=visible_only,
                names_only=names_only,
                show_query=show_query,
                count_only=count_only,
            )
            # Collect statistics during extraction
            stats: dict[str, Any] = {"total": 0, "by_role": {}}
            async for item in extract_a11y_view(page, options):
                item["req_id"] = request.req_id
                # Track stats
                stats["total"] += 1
                role = item.get("role", "unknown")
                stats["by_role"][role] = stats["by_role"].get(role, 0) + 1
                # Only yield items if not count_only mode
                if not count_only:
                    yield ItemResponse(
                        req_id=request.req_id,
                        view="a11y",
                        data=item,
                    )
            # Include stats in done response
            yield DoneResponse(req_id=request.req_id, ok=True, summary=stats)

        elif view == "md":
            async for item in extract_markdown_view(page):
                item["req_id"] = request.req_id
                yield ItemResponse(
                    req_id=request.req_id,
                    view="md",
                    data=item,
                )
            yield DoneResponse(req_id=request.req_id, ok=True)

        elif view == "dom-lite":
            dom_lite_options = DomLiteOptions()
            async for item in extract_dom_lite_view(page, dom_lite_options):
                item["req_id"] = request.req_id
                yield ItemResponse(
                    req_id=request.req_id,
                    view="dom-lite",
                    data=item,
                )
            yield DoneResponse(req_id=request.req_id, ok=True)

        else:
            yield ErrorResponse(
                req_id=request.req_id,
                error=f"Unknown view type: {view}",
                code="invalid_view",
            )
            return

    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("screenshot")
async def handle_screenshot(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Take a screenshot of the current page."""
    session_id = request.args.get("session", "default")
    path = request.args.get("path")
    full_page = request.args.get("full_page", False)

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        if path:
            # Save to file
            await page.screenshot(path=path, full_page=full_page)
            yield DoneResponse(
                req_id=request.req_id,
                ok=True,
                summary={"path": path},
            )
        else:
            # Return as base64
            screenshot_bytes = await page.screenshot(full_page=full_page)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            yield ItemResponse(
                req_id=request.req_id,
                view="screenshot",
                data={
                    "format": "png",
                    "encoding": "base64",
                    "data": screenshot_b64,
                },
            )
            yield DoneResponse(req_id=request.req_id, ok=True)

    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("page.info")
async def handle_page_info(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Get information about the current page."""
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
        yield ItemResponse(
            req_id=request.req_id,
            view="page_info",
            data={
                "url": page.url,
                "title": await page.title(),
            },
        )
        yield DoneResponse(req_id=request.req_id, ok=True)

    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("query")
async def handle_query(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Debug a query by showing all matches and suggestions."""
    session_id = request.args.get("session", "default")
    query_str = request.args.get("query", "")

    page = session_manager.get_active_page(session_id)
    if not page:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    # Parse query
    try:
        parse_query(query_str)
    except ParseError as e:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Invalid query syntax: {e}",
            code="parse_error",
        )
        return

    # Get snapshot
    try:
        snapshot_str = await page.locator("body").aria_snapshot()
    except Exception:
        snapshot_str = ""

    if not snapshot_str:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Could not get page snapshot",
            code="snapshot_error",
        )
        return

    items = parse_aria_snapshot(snapshot_str)

    # Collect all roles and names for suggestions
    all_roles: set[str] = set()
    all_names: list[str] = []
    for item in items:
        if item.get("role"):
            all_roles.add(item["role"])
        if item.get("name"):
            all_names.append(item["name"])

    # Simple matching based on query type
    matches = []
    query_role = None
    query_name = None

    # Extract role and name from query string for matching
    import re

    role_match = re.search(r"role=(\w+)", query_str)
    if role_match:
        query_role = role_match.group(1).lower()

    name_match = re.search(r'name[~]?=(["\']?)([^"\']+)\1', query_str)
    if name_match:
        query_name = name_match.group(2)

    name_regex_match = re.search(r'name~=(["\']?)([^"\']+)\1', query_str)
    is_name_regex = name_regex_match is not None

    # Filter items
    for item in items:
        item_role = item.get("role", "").lower()
        item_name = item.get("name", "")

        role_matches = query_role is None or item_role == query_role
        name_matches = True
        if query_name:
            if is_name_regex:
                name_matches = query_name.lower() in item_name.lower()
            else:
                name_matches = item_name == query_name

        if role_matches and name_matches:
            matches.append(item)

    # Build suggestions if no matches
    suggestions = []
    if not matches:
        # Role suggestions
        if query_role and query_role not in all_roles:
            similar_roles = get_close_matches(query_role, list(all_roles), n=3, cutoff=0.6)
            if similar_roles:
                suggestions.append(
                    f"Role '{query_role}' not found. Did you mean: {', '.join(similar_roles)}?"
                )
            else:
                INTERACTIVE_ROLES | LANDMARK_ROLES | STRUCTURAL_ROLES
                suggestions.append(
                    f"Role '{query_role}' not found. Available roles on page: {', '.join(sorted(all_roles)[:10])}"
                )

        # Name suggestions
        if query_name:
            similar_names = get_close_matches(query_name, all_names, n=3, cutoff=0.5)
            if similar_names:
                suggestions.append(f"No exact name match. Similar names: {similar_names}")
            if not is_name_regex:
                suggestions.append('Try name~="pattern" for partial/regex matching')

    # Yield results
    yield ItemResponse(
        req_id=request.req_id,
        view="query_debug",
        data={
            "query": query_str,
            "match_count": len(matches),
            "matches": [
                {
                    "id": m.get("id"),
                    "role": m.get("role"),
                    "name": m.get("name", ""),
                    "enabled": m.get("enabled", True),
                    "disabled": m.get("disabled", False),
                    "checked": m.get("checked"),
                    "level": m.get("level"),
                }
                for m in matches[:20]  # Limit to 20 matches
            ],
            "suggestions": suggestions,
            "available_roles": sorted(all_roles),
        },
    )
    yield DoneResponse(req_id=request.req_id, ok=True)


@register("inspect")
async def handle_inspect(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Inspect an element's full attributes for debugging."""
    from typing import cast

    from .interact import ResolveError, resolve_with_fallback

    session_id = request.args.get("session", "default")
    query_str = request.args.get("query", "")

    if not query_str:
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
        result = await resolve_with_fallback(page, query_str)
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

        # Use pre-built locator from fallback, or build from role/name
        if "_locator" in element:
            locator = element["_locator"]
        else:
            role = element.get("role")
            name = element.get("name")
            locator = (
                page.get_by_role(cast(Any, role), name=name)
                if name
                else page.get_by_role(cast(Any, role))
            )

        # Get the first matching element
        el = locator.first

        # Fetch all attributes
        info: dict[str, Any] = {
            "id": element.get("id"),
            "role": element.get("role", "unknown"),
            "name": element.get("name", ""),
            "resolved_by": result.method,
        }

        # Fetch HTML attributes
        try:
            info["aria-label"] = await el.get_attribute("aria-label")
        except Exception:
            info["aria-label"] = None

        try:
            info["title"] = await el.get_attribute("title")
        except Exception:
            info["title"] = None

        try:
            info["placeholder"] = await el.get_attribute("placeholder")
        except Exception:
            info["placeholder"] = None

        try:
            info["class"] = await el.get_attribute("class")
        except Exception:
            info["class"] = None

        try:
            info["id_attr"] = await el.get_attribute("id")
        except Exception:
            info["id_attr"] = None

        try:
            info["data-testid"] = await el.get_attribute("data-testid")
        except Exception:
            info["data-testid"] = None

        # Fetch state
        try:
            info["visible"] = await el.is_visible()
        except Exception:
            info["visible"] = None

        try:
            info["enabled"] = await el.is_enabled()
        except Exception:
            info["enabled"] = None

        try:
            info["editable"] = await el.is_editable()
        except Exception:
            info["editable"] = None

        try:
            info["checked"] = await el.is_checked()
        except Exception:
            info["checked"] = None

        # Get bounding box
        try:
            bbox = await el.bounding_box()
            if bbox:
                info["bbox"] = {
                    "x": round(bbox["x"], 1),
                    "y": round(bbox["y"], 1),
                    "width": round(bbox["width"], 1),
                    "height": round(bbox["height"], 1),
                }
        except Exception:
            info["bbox"] = None

        # Get inner text (truncated)
        try:
            text = await el.inner_text()
            if text:
                info["inner_text"] = text[:200] + ("..." if len(text) > 200 else "")
        except Exception:
            info["inner_text"] = None

        # Add warning if present
        if result.warning:
            info["note"] = result.warning

        yield ItemResponse(
            req_id=request.req_id,
            view="inspect",
            data=info,
        )
        yield DoneResponse(req_id=request.req_id, ok=True)

    except Exception as e:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Inspect failed: {e}",
            code="inspect_failed",
        )
