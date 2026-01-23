"""
RFC SS8.1: Action View (a11y) - MUST

Contains:
- interactive elements only
- role
- accessible name
- states (enabled, checked, expanded, required)
- stable id
- optional bbox (bounding box)
- optional path_hint
"""

import hashlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page

from .filters import SnapshotFilter, filter_a11y_items, parse_roles_string
from .redaction import redact_if_sensitive


@dataclass
class A11yExtractOptions:
    """Options for a11y view extraction."""

    include_bbox: bool = False
    include_path_hint: bool = True
    interesting_only: bool = True
    # Filtering options
    max_depth: int | None = None
    limit: int | None = None
    roles: str | None = None  # Comma-separated roles
    interactive_only: bool = False
    within: str | None = None  # Query to scope snapshot (e.g., "role=main")
    # Large output handling
    grep_pattern: str | None = None  # Regex pattern to filter by role+name
    max_name_length: int | None = None  # Truncate names longer than this
    visible_only: bool = (
        False  # If True, filter to viewport (expensive - requires bbox lookup per element)
    )
    names_only: bool = False  # Only output role and name (no states/attributes)
    show_query: bool = False  # Include the query string to target each element
    count_only: bool = False  # Only return stats, no items


def parse_aria_snapshot(snapshot: str) -> list[dict[str, Any]]:
    """
    Parse Playwright's aria_snapshot YAML-like format into structured data.

    Format example:
    - heading "Example Domain" [level=1]
    - paragraph: This is some text
    - link "Click here":
      - /url: https://example.com
    """
    items: list[dict[str, Any]] = []
    lines = snapshot.strip().split("\n")
    counter = 0

    for line in lines:
        if not line.strip():
            continue

        # Calculate depth based on indentation
        depth = (len(line) - len(line.lstrip())) // 2

        line = line.strip()

        # Skip property lines (start with /)
        if line.startswith("- /") or line.startswith("/"):
            continue

        if not line.startswith("-"):
            continue

        line = line[2:]  # Remove "- " prefix

        # Parse role and name
        # Format: role "name" [attributes]: content
        # or: role "name" [attributes]
        # or: role: content

        counter += 1
        item: dict[str, Any] = {
            "id": f"n{counter}",
            "enabled": True,
        }

        # Extract role
        match = re.match(r"^(\w+)", line)
        if match:
            item["role"] = match.group(1)
            line = line[len(match.group(0)) :].strip()

        # Extract quoted name
        if line.startswith('"'):
            end_quote = line.find('"', 1)
            if end_quote > 0:
                item["name"] = line[1:end_quote]
                line = line[end_quote + 1 :].strip()

        # Extract attributes in brackets
        if line.startswith("["):
            end_bracket = line.find("]")
            if end_bracket > 0:
                attrs_str = line[1:end_bracket]
                # Parse attributes like level=1, checked=true
                for attr in attrs_str.split(","):
                    attr = attr.strip()
                    if "=" in attr:
                        key, value = attr.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        if value == "true":
                            item[key] = True
                        elif value == "false":
                            item[key] = False
                        elif value.isdigit():
                            item[key] = int(value)
                        else:
                            item[key] = value
                line = line[end_bracket + 1 :].strip()

        # Extract content after colon
        if line.startswith(":"):
            content = line[1:].strip()
            if content and "name" not in item:
                item["description"] = content

        # Add depth for path calculation
        item["_depth"] = depth

        items.append(item)

    return items


def _filter_within_scope(items: list[dict[str, Any]], within_query: str) -> list[dict[str, Any]]:
    """
    Filter items to only include elements within a specified container.

    Args:
        items: List of a11y items with _depth field
        within_query: Query like "role=main" or "role=dialog name~=Settings"

    Returns:
        Filtered list of items that are descendants of the matched element
    """
    # Parse the within query to extract role and name
    query_role = None
    query_name = None
    query_name_partial = False

    role_match = re.search(r"role=(\w+)", within_query)
    if role_match:
        query_role = role_match.group(1).lower()

    name_match = re.search(r'name~=["\']?([^"\']+)["\']?', within_query)
    if name_match:
        query_name = name_match.group(1)
        query_name_partial = True
    else:
        name_match = re.search(r'name=["\']?([^"\']+)["\']?', within_query)
        if name_match:
            query_name = name_match.group(1)
            query_name_partial = False

    if not query_role:
        return items  # No valid query, return all items

    # Find the container element
    container_idx = None
    container_depth = None

    for idx, item in enumerate(items):
        item_role = item.get("role", "").lower()
        item_name = item.get("name", "")

        if item_role == query_role:
            if query_name:
                if query_name_partial:
                    if query_name.lower() in item_name.lower():
                        container_idx = idx
                        container_depth = item.get("_depth", 0)
                        break
                else:
                    if item_name == query_name:
                        container_idx = idx
                        container_depth = item.get("_depth", 0)
                        break
            else:
                container_idx = idx
                container_depth = item.get("_depth", 0)
                break

    if container_idx is None:
        return []  # Container not found

    # Collect the container and all its descendants
    result = [items[container_idx]]

    for item in items[container_idx + 1 :]:
        item_depth = item.get("_depth", 0)
        # Stop when we reach an element at the same or lesser depth
        if item_depth <= container_depth:
            break
        result.append(item)

    return result


async def extract_a11y_view(
    page: Page, options: A11yExtractOptions | None = None
) -> AsyncIterator[dict[str, Any]]:
    """
    Extract accessibility tree as JSONL items.

    RFC SS8.1 fields:
    - id: stable identifier
    - role: ARIA role
    - name: accessible name
    - enabled, checked, expanded, required: states
    - bbox: {x, y, width, height} (optional)
    - path_hint: semantic path in tree (optional)
    """
    options = options or A11yExtractOptions()

    # Use the new aria_snapshot API
    try:
        snapshot_str = await page.locator("body").aria_snapshot()
    except Exception:
        snapshot_str = ""

    if not snapshot_str:
        return

    items = parse_aria_snapshot(snapshot_str)

    # Apply --within scoping if specified
    if options.within:
        items = _filter_within_scope(items, options.within)

    # Build filter config from options
    filter_config = SnapshotFilter(
        max_depth=options.max_depth,
        limit=options.limit,
        roles=parse_roles_string(options.roles) if options.roles else None,
        interactive_only=options.interactive_only,
        grep_pattern=options.grep_pattern,
        max_name_length=options.max_name_length,
    )

    # Build path hints
    path_stack: list[tuple[int, str]] = []
    processed_items: list[dict[str, Any]] = []

    for item in items:
        depth = item.get("_depth", 0)

        # Maintain path stack
        while path_stack and path_stack[-1][0] >= depth:
            path_stack.pop()

        # Build path segment
        path_segment = item.get("role", "unknown")
        name = item.get("name", "")
        if name:
            short_name = name[:30] + "..." if len(name) > 30 else name
            path_segment += f'["{short_name}"]'

        path_stack.append((depth, path_segment))

        # Build path_hint before we potentially filter
        path_hint = " > ".join(seg for _, seg in path_stack)

        # Redact sensitive content
        if name:
            is_password = item.get("role") == "textbox" and "password" in name.lower()
            item["name"] = redact_if_sensitive(name, is_password)

        # Add path_hint
        if options.include_path_hint:
            item["path_hint"] = path_hint

        processed_items.append(item)

    # Apply filtering
    filtered_items = filter_a11y_items(iter(processed_items), filter_config)

    for item in filtered_items:
        # Remove internal depth field
        item.pop("_depth", None)

        # Apply visible_only filter (check if element is in viewport)
        if options.visible_only:
            bbox = await _get_element_bbox(page, item)
            if bbox:
                viewport = page.viewport_size
                if viewport:
                    # Check if element is outside viewport
                    if (
                        bbox["x"] + bbox["width"] < 0
                        or bbox["y"] + bbox["height"] < 0
                        or bbox["x"] > viewport["width"]
                        or bbox["y"] > viewport["height"]
                    ):
                        continue  # Skip off-viewport elements
                # Add bbox if requested
                if options.include_bbox:
                    item["bbox"] = bbox
        elif options.include_bbox:
            # Add bbox if requested (expensive)
            bbox = await _get_element_bbox(page, item)
            if bbox:
                item["bbox"] = bbox

        # Build final item
        if options.names_only:
            # Strip to just role and name for minimal output
            result: dict[str, Any] = {
                "type": "item",
                "view": "a11y",
                "id": item.get("id", ""),
                "role": item.get("role", ""),
                "name": item.get("name", ""),
            }
        else:
            result = {
                "type": "item",
                "view": "a11y",
                **item,
            }

        # Add query string if requested
        if options.show_query:
            role = item.get("role", "")
            name = item.get("name", "")
            if name:
                # Escape quotes in name and use partial match
                escaped_name = name.replace('"', '\\"')
                result["query"] = f'role={role} name~="{escaped_name}"'
            else:
                result["query"] = f"role={role}"

        yield result


async def _get_element_bbox(page: Page, item: dict[str, Any]) -> dict[str, float] | None:
    """
    Get bounding box for an a11y item.

    Uses role + name to find matching element.
    """
    role = item.get("role")
    name = item.get("name")

    if not role:
        return None

    try:
        locator = page.get_by_role(role, name=name) if name else page.get_by_role(role)

        count = await locator.count()
        if count > 0:
            bbox = await locator.first.bounding_box()
            if bbox:
                return {
                    "x": round(bbox["x"], 1),
                    "y": round(bbox["y"], 1),
                    "width": round(bbox["width"], 1),
                    "height": round(bbox["height"], 1),
                }
    except Exception:
        pass

    return None


async def get_a11y_snapshot(page: Page, interesting_only: bool = True) -> dict[str, Any] | None:
    """Get raw a11y tree snapshot as a dict."""
    try:
        snapshot_str = await page.locator("body").aria_snapshot()
        items = parse_aria_snapshot(snapshot_str)
        return {"items": items, "raw": snapshot_str}
    except Exception:
        return None


async def get_a11y_snapshot_hash(page: Page) -> str:
    """Get a hash of the current a11y tree for change detection."""
    try:
        snapshot_str = await page.locator("body").aria_snapshot()
        if snapshot_str:
            return hashlib.sha256(snapshot_str.encode()).hexdigest()[:16]
    except Exception:
        pass
    return ""
