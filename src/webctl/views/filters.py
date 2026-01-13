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


@dataclass
class SnapshotFilter:
    """Configuration for filtering a11y snapshots."""

    max_depth: int | None = None
    limit: int | None = None
    roles: set[str] | None = None
    interactive_only: bool = False
    include_landmarks: bool = True  # Include landmarks even with interactive_only

    def is_active(self) -> bool:
        """Check if any filtering is configured."""
        return any(
            [
                self.max_depth is not None,
                self.limit is not None,
                self.roles is not None,
                self.interactive_only,
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
    """
    Filter a11y items based on configuration.

    Args:
        items: Iterator of a11y item dicts
        filter_config: Filtering configuration

    Yields:
        Filtered items
    """
    if not filter_config.is_active():
        yield from items
        return

    count = 0

    for item in items:
        # Check limit
        if filter_config.limit is not None and count >= filter_config.limit:
            return

        # Check depth (if available in item)
        depth = item.get("_depth", 0)
        if filter_config.max_depth is not None and depth > filter_config.max_depth:
            continue

        # Check role
        role = item.get("role", "")
        if not filter_config.should_include_role(role):
            continue

        count += 1
        yield item


def parse_roles_string(roles_str: str) -> set[str]:
    """Parse comma-separated roles string into a set."""
    if not roles_str:
        return set()
    return {r.strip().lower() for r in roles_str.split(",") if r.strip()}
