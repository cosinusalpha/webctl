"""
Export and diff functionality for views.

RFC SS14: Export/diff support.
"""

import json
from pathlib import Path
from typing import Any

from deepdiff import DeepDiff


async def export_view(view_data: list[dict[str, Any]], output_path: Path) -> None:
    """Export view data to a file."""
    with open(output_path, "w", encoding="utf-8") as f:
        for item in view_data:
            f.write(json.dumps(item) + "\n")


async def import_view(input_path: Path) -> list[dict[str, Any]]:
    """Import view data from a file."""
    items: list[dict[str, Any]] = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def diff_views(
    old_data: list[dict[str, Any]], new_data: list[dict[str, Any]], verbose: bool = False
) -> dict[str, Any]:
    """
    Compare two view snapshots and return differences.

    Returns a summary of changes including:
    - added: items in new but not in old
    - removed: items in old but not in new
    - changed: items that exist in both but are different
    """
    # Create lookup by ID for comparison
    old_by_id = {item.get("id"): item for item in old_data if item.get("id")}
    new_by_id = {item.get("id"): item for item in new_data if item.get("id")}

    old_ids = set(old_by_id.keys())
    new_ids = set(new_by_id.keys())

    added_ids = new_ids - old_ids
    removed_ids = old_ids - new_ids
    common_ids = old_ids & new_ids

    added = [new_by_id[id] for id in added_ids]
    removed = [old_by_id[id] for id in removed_ids]

    changed: list[dict[str, Any]] = []
    for id in common_ids:
        old_item = old_by_id[id]
        new_item = new_by_id[id]

        # Skip internal fields when comparing
        old_clean = {k: v for k, v in old_item.items() if not k.startswith("_")}
        new_clean = {k: v for k, v in new_item.items() if not k.startswith("_")}

        if old_clean != new_clean:
            diff_detail = None
            if verbose:
                diff_detail = DeepDiff(old_clean, new_clean, ignore_order=True)

            changed.append(
                {
                    "id": id,
                    "old": old_item,
                    "new": new_item,
                    "diff": diff_detail.to_dict() if diff_detail else None,
                }
            )

    return {
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
            "unchanged_count": len(common_ids) - len(changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def format_diff_summary(diff_result: dict[str, Any]) -> str:
    """Format diff result as human-readable summary."""
    summary = diff_result["summary"]
    lines = [
        f"Added: {summary['added_count']}",
        f"Removed: {summary['removed_count']}",
        f"Changed: {summary['changed_count']}",
        f"Unchanged: {summary['unchanged_count']}",
    ]

    if diff_result["added"]:
        lines.append("\nAdded elements:")
        for item in diff_result["added"][:5]:
            role = item.get("role", "unknown")
            name = item.get("name", "")[:30]
            lines.append(f"  + {role}: {name}")
        if len(diff_result["added"]) > 5:
            lines.append(f"  ... and {len(diff_result['added']) - 5} more")

    if diff_result["removed"]:
        lines.append("\nRemoved elements:")
        for item in diff_result["removed"][:5]:
            role = item.get("role", "unknown")
            name = item.get("name", "")[:30]
            lines.append(f"  - {role}: {name}")
        if len(diff_result["removed"]) > 5:
            lines.append(f"  ... and {len(diff_result['removed']) - 5} more")

    if diff_result["changed"]:
        lines.append("\nChanged elements:")
        for change in diff_result["changed"][:5]:
            old_name = change["old"].get("name", "")[:20]
            new_name = change["new"].get("name", "")[:20]
            lines.append(f"  ~ {change['id']}: {old_name} -> {new_name}")
        if len(diff_result["changed"]) > 5:
            lines.append(f"  ... and {len(diff_result['changed']) - 5} more")

    return "\n".join(lines)
