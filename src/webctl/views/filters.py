"""
Snapshot filtering utilities for reducing output size.

Provides filtering by:
- max_depth: Limit tree traversal depth
- limit: Maximum number of nodes to return
- roles: Filter to specific ARIA roles
- interactive_only: Only return interactive elements
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

# Interactive roles that can be acted upon
INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "combobox",
        "checkbox",
        "radio",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "menuitem",
        "option",
        "searchbox",
        "listbox",
        "menu",
        "menubar",
        "tree",
        "treeitem",
        "gridcell",
        "row",
        "columnheader",
        "rowheader",
    }
)

# Landmark roles for page structure
LANDMARK_ROLES = frozenset(
    {"banner", "navigation", "main", "contentinfo", "complementary", "search", "form", "region"}
)

# Structural roles that provide context
STRUCTURAL_ROLES = frozenset(
    {
        "heading",
        "list",
        "listitem",
        "table",
        "grid",
        "tablist",
        "toolbar",
        "dialog",
        "alertdialog",
        "alert",
    }
)

# Combined set for navigate: interactive + landmarks + key structural (no list/listitem noise)
NAVIGATE_ROLES = (
    INTERACTIVE_ROLES
    | LANDMARK_ROLES
    | frozenset(
        {"heading", "table", "grid", "tablist", "toolbar", "dialog", "alertdialog", "alert"}
    )
)

# Extended set for main landmark content: includes article/feed/text/img so that
# search result cards (Maps, Amazon, etc.) are complete in a single snapshot.
_MAIN_CONTENT_ROLES = NAVIGATE_ROLES | frozenset({"article", "feed", "text", "img"})


@dataclass
class SnapshotFilter:
    """Configuration for filtering a11y snapshots."""

    max_depth: int | None = None
    limit: int | None = None
    roles: set[str] | None = None
    interactive_only: bool = False
    include_landmarks: bool = True  # Include landmarks even with interactive_only
    grep_pattern: str | None = None  # Regex pattern to filter by role+name
    max_name_length: int | None = None  # Truncate names longer than this

    def is_active(self) -> bool:
        """Check if any filtering is configured."""
        return any(
            [
                self.max_depth is not None,
                self.limit is not None,
                self.roles is not None,
                self.interactive_only,
                self.grep_pattern is not None,
                self.max_name_length is not None,
            ]
        )

    def should_include_role(self, role: str) -> bool:
        """Check if a role passes the filter criteria."""
        if self.roles is not None:
            return role in self.roles

        if self.interactive_only:
            if role in INTERACTIVE_ROLES:
                return True
            return bool(self.include_landmarks and role in LANDMARK_ROLES)

        return True


def filter_a11y_items(
    items: Iterator[dict[str, Any]],
    filter_config: SnapshotFilter,
) -> Iterator[dict[str, Any]]:
    """Filter a11y items based on configuration."""
    import re

    # Compile grep pattern if provided
    grep_regex = None
    if filter_config.grep_pattern:
        try:
            grep_regex = re.compile(filter_config.grep_pattern, re.IGNORECASE)
        except re.error:
            # Invalid regex, treat as literal string
            grep_regex = re.compile(re.escape(filter_config.grep_pattern), re.IGNORECASE)

    if not filter_config.is_active():
        yield from items
        return

    count = 0

    for item in items:
        if filter_config.limit is not None and count >= filter_config.limit:
            return

        depth = item.get("_depth", 0)
        if filter_config.max_depth is not None and depth > filter_config.max_depth:
            continue

        role = item.get("role", "")
        if not filter_config.should_include_role(role):
            continue

        # Apply grep filter
        if grep_regex:
            name = item.get("name", "")
            searchable = f"{role} {name}"
            if not grep_regex.search(searchable):
                continue

        # Apply name truncation
        if filter_config.max_name_length:
            name = item.get("name", "")
            if name and len(name) > filter_config.max_name_length:
                item["name"] = name[: filter_config.max_name_length - 3] + "..."

        count += 1
        yield item


# --- Landmark-aware filtering for smart navigate snapshots ---

# Landmarks shown fully expanded
_EXPAND_LANDMARKS = frozenset({"main"})
# Landmarks shown with interactive children only (compact)
_COMPACT_LANDMARKS = frozenset({"search", "form"})
# Landmarks collapsed to just the landmark + count
_COLLAPSE_LANDMARKS = frozenset({"banner", "navigation", "complementary", "region"})
# Landmarks hidden entirely
_HIDE_LANDMARKS = frozenset({"contentinfo"})
# Roles that always surface first (blocking UI)
_PRIORITY_ROLES = frozenset({"dialog", "alertdialog", "alert"})


def landmark_aware_filter(
    items: list[dict[str, Any]], allowed_roles: frozenset[str] | None = None
) -> list[dict[str, Any]]:
    """Filter items by landmark context for a smart navigate snapshot.

    Priority order in output:
    1. dialog/alertdialog/alert (blocking UI)
    2. search landmark (compact — interactive children only)
    3. main landmark (fully expanded)
    4. form outside main (compact)
    5. banner/navigation/complementary/region (collapsed — landmark + count)
    6. contentinfo — hidden

    If no main landmark exists, returns all items (with allowed_roles filter).
    """
    if not items:
        return items

    if allowed_roles is None:
        allowed_roles = NAVIGATE_ROLES

    # Always partition by landmark context — even without a "main" landmark,
    # we still benefit from collapsing banner/navigation/complementary and
    # extracting search. Orphan items (not inside any landmark) serve as the
    # primary content when no "main" exists.

    # Pre-compute: large dialogs (>20 children) are content panels, not blocking modals —
    # collapse them like complementary/region instead of expanding as priority
    large_dialog_threshold = 20
    large_dialog_indices: set[int] = set()
    for idx, item in enumerate(items):
        if item.get("role") in _PRIORITY_ROLES:
            d_depth = item.get("_depth", 0)
            count = 0
            for j in range(idx + 1, len(items)):
                if items[j].get("_depth", 0) <= d_depth:
                    break
                count += 1
            if count > large_dialog_threshold:
                large_dialog_indices.add(idx)

    # Partition items by landmark context
    priority_items: list[dict[str, Any]] = []  # dialog/alert (small, blocking)
    search_items: list[dict[str, Any]] = []
    main_items: list[dict[str, Any]] = []
    compact_items: list[dict[str, Any]] = []  # form outside main
    collapsed_landmarks: list[dict[str, Any]] = []  # banner/nav/complementary — just landmarks
    orphan_items: list[dict[str, Any]] = []  # items not inside any landmark

    child_count: dict[int, int] = {}  # id(landmark_item) -> count of children

    # Stack of (effective_role, depth, collapsed_id_or_None)
    # effective_role: the landmark role used for routing (e.g. "complementary" for large dialogs)
    # collapsed_id: id() of the landmark item in collapsed_landmarks, for child counting
    landmark_stack: list[tuple[str, int, int | None]] = []

    for idx, item in enumerate(items):
        depth = item.get("_depth", 0)
        role = item.get("role", "")

        # Pop landmarks we've exited (item at same or shallower depth)
        while landmark_stack and depth <= landmark_stack[-1][1]:
            landmark_stack.pop()

        # Enter new landmark or priority role
        if role in LANDMARK_ROLES or role in _PRIORITY_ROLES:
            collapsed_id: int | None = None
            effective_role = role

            if role in _PRIORITY_ROLES and idx not in large_dialog_indices:
                priority_items.append(item)
            elif role in _PRIORITY_ROLES:
                # Large dialog/alert — collapse like complementary
                collapsed_landmarks.append(item)
                child_count[id(item)] = 0
                collapsed_id = id(item)
                effective_role = "complementary"  # route children to collapse
            elif role == "search":
                search_items.append(item)
            elif role == "main":
                main_items.append(item)
            elif role in _COMPACT_LANDMARKS:
                compact_items.append(item)
            elif role in _COLLAPSE_LANDMARKS:
                collapsed_landmarks.append(item)
                child_count[id(item)] = 0
                collapsed_id = id(item)
            # contentinfo: skip entirely (but still push to stack so children are hidden)

            landmark_stack.append((effective_role, depth, collapsed_id))
            continue

        # Current context = top of stack (or None if orphan)
        if not landmark_stack:
            current_landmark = None
            current_collapsed_id = None
        else:
            current_landmark = landmark_stack[-1][0]
            current_collapsed_id = landmark_stack[-1][2]

        # Route item based on current landmark context
        if current_landmark is None:
            # Orphan — not inside any landmark
            if role in allowed_roles:
                orphan_items.append(item)
        elif current_landmark in _PRIORITY_ROLES:
            if role in allowed_roles:
                priority_items.append(item)
        elif current_landmark == "search":
            if role in INTERACTIVE_ROLES:
                search_items.append(item)
        elif current_landmark == "main":
            if role in _MAIN_CONTENT_ROLES:
                main_items.append(item)
        elif current_landmark in _COMPACT_LANDMARKS:
            if role in INTERACTIVE_ROLES:
                compact_items.append(item)
        elif current_landmark in _COLLAPSE_LANDMARKS and current_collapsed_id is not None:
            child_count[current_collapsed_id] += 1
        # contentinfo children: skip

    # Annotate collapsed landmarks with child count
    for lm in collapsed_landmarks:
        count = child_count.get(id(lm), 0)
        if count > 0:
            name = lm.get("name", "")
            lm["name"] = f"{name} ({count} items)" if name else f"({count} items)"

    return (
        priority_items
        + search_items
        + main_items
        + compact_items
        + collapsed_landmarks
        + orphan_items
    )


# Roles whose children should be collapsed when there are too many
COLLAPSIBLE_CONTAINER_ROLES = frozenset({"combobox", "listbox", "menu", "menubar", "tree"})
COLLAPSIBLE_CHILD_ROLES = frozenset({"option", "menuitem", "treeitem"})


def collapse_containers(items: list[dict[str, Any]], threshold: int = 5) -> list[dict[str, Any]]:
    """Collapse children of combobox/listbox/menu/tree when count exceeds threshold.

    Keeps the first 3 children for context, drops the rest, and annotates
    the parent with the total count (e.g., "(52 options)").
    """
    if not items:
        return items

    result: list[dict[str, Any]] = []
    # Track active container: (depth, child_count, result_index)
    container_stack: list[tuple[int, int, int]] = []

    for item in items:
        depth = item.get("_depth", 0)
        role = item.get("role", "")

        # Pop containers we've exited (depth <= container depth)
        while container_stack and depth <= container_stack[-1][0]:
            c_depth, c_count, c_idx = container_stack.pop()
            if c_count > threshold:
                parent = result[c_idx]
                name = parent.get("name", "")
                parent["name"] = f"{name} ({c_count} items)" if name else f"({c_count} items)"

        if role in COLLAPSIBLE_CONTAINER_ROLES:
            container_stack.append((depth, 0, len(result)))
            result.append(item)
            continue

        if container_stack and role in COLLAPSIBLE_CHILD_ROLES:
            # Increment child count on innermost container
            c_depth, c_count, c_idx = container_stack[-1]
            c_count += 1
            container_stack[-1] = (c_depth, c_count, c_idx)

            if c_count <= 3:
                result.append(item)  # keep first 3
            # else: skip (collapsed)
            continue

        result.append(item)

    # Finalize any remaining open containers
    for _c_depth, c_count, c_idx in container_stack:
        if c_count > threshold:
            parent = result[c_idx]
            name = parent.get("name", "")
            parent["name"] = f"{name} ({c_count} items)" if name else f"({c_count} items)"

    return result


def deduplicate_adjacent(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove adjacent link+heading pairs with the same name, keeping the link."""
    if not items:
        return items
    result: list[dict[str, Any]] = []
    skip_next = False
    for i, item in enumerate(items):
        if skip_next:
            skip_next = False
            continue
        if i + 1 < len(items):
            nxt = items[i + 1]
            if (
                item.get("name")
                and item["name"] == nxt.get("name")
                and {item.get("role"), nxt.get("role")} == {"link", "heading"}
            ):
                # Keep the link (clickable), skip the heading
                result.append(item if item["role"] == "link" else nxt)
                skip_next = True
                continue
        result.append(item)
    return result


def parse_roles_string(roles_str: str) -> set[str]:
    """Parse comma-separated roles string into a set."""
    if not roles_str:
        return set()
    return {r.strip().lower() for r in roles_str.split(",") if r.strip()}
