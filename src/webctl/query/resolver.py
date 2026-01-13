"""
Query resolver for matching queries against the accessibility tree.
"""

import math
import re
from dataclasses import dataclass
from typing import Any

from ..exceptions import AmbiguousTargetError, NoMatchError
from .ast import (
    AndExpr,
    IdFilter,
    NameFilter,
    NearExpr,
    NthExpr,
    OrExpr,
    QueryExpr,
    RoleFilter,
    StateFilter,
    TextFilter,
    WithinExpr,
)


@dataclass
class BoundingBox:
    """Bounding box for element positioning."""

    x: float
    y: float
    width: float
    height: float

    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def distance_to(self, other: "BoundingBox") -> float:
        """Calculate minimum distance between two bounding boxes."""
        cx1, cy1 = self.center()
        cx2, cy2 = other.center()
        return math.sqrt((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2)


@dataclass
class ResolveResult:
    """Result of query resolution."""

    matches: list[dict[str, Any]]
    count: int


class QueryResolver:
    """Resolve queries against an accessibility tree."""

    def __init__(self, a11y_tree: dict[str, Any], strict: bool = True):
        self.tree = a11y_tree
        self.strict = strict
        self._flat_nodes: list[dict[str, Any]] | None = None

    def resolve(self, query: QueryExpr) -> ResolveResult:
        """Resolve query against a11y tree."""
        candidates = self._flatten()
        matches = self._apply(candidates, query)

        if len(matches) == 0:
            raise NoMatchError("No element matches query")

        if self.strict and len(matches) > 1:
            raise AmbiguousTargetError(
                f"Query matched {len(matches)} elements (strict mode)",
                matches=matches[:5],
            )

        return ResolveResult(matches=matches, count=len(matches))

    def _flatten(self) -> list[dict[str, Any]]:
        """Flatten tree to list of nodes with parent refs and path."""
        if self._flat_nodes is not None:
            return self._flat_nodes

        nodes = []

        def walk(
            node: dict[str, Any],
            parent: dict[str, Any] | None = None,
            depth: int = 0,
            path: list[str] | None = None,
        ) -> None:
            path = path or []

            # Build path segment
            path_segment = node.get("role", "unknown")
            if node.get("name"):
                path_segment += f'[name="{node["name"][:20]}"]'
            current_path = path + [path_segment]

            # Convert bbox dict to BoundingBox if present
            bbox = node.get("bbox")
            if bbox and isinstance(bbox, dict):
                bbox = BoundingBox(
                    x=bbox.get("x", 0),
                    y=bbox.get("y", 0),
                    width=bbox.get("width", 0),
                    height=bbox.get("height", 0),
                )

            node_copy = {
                **node,
                "_parent": parent,
                "_depth": depth,
                "_path": current_path,
                "_path_hint": " > ".join(current_path),
                "_bbox": bbox,
            }
            nodes.append(node_copy)

            for child in node.get("children", []):
                walk(child, node_copy, depth + 1, current_path)

        walk(self.tree)
        self._flat_nodes = nodes
        return nodes

    def _apply(self, candidates: list[dict[str, Any]], query: QueryExpr) -> list[dict[str, Any]]:
        """Apply query filter to candidates."""
        match query:
            case RoleFilter(role=r):
                return [c for c in candidates if c.get("role") == r]

            case NameFilter(pattern=p, is_regex=True):
                regex = re.compile(p, re.IGNORECASE)
                return [c for c in candidates if regex.search(c.get("name", ""))]

            case NameFilter(pattern=p, is_regex=False):
                return [c for c in candidates if c.get("name") == p]

            case TextFilter(pattern=p, is_regex=True):
                regex = re.compile(p, re.IGNORECASE)
                return [
                    c
                    for c in candidates
                    if regex.search(c.get("name", "") + " " + c.get("description", ""))
                ]

            case TextFilter(pattern=p, is_regex=False):
                return [
                    c
                    for c in candidates
                    if p in (c.get("name", "") + " " + c.get("description", ""))
                ]

            case IdFilter(id=id_val):
                return [c for c in candidates if c.get("id") == id_val]

            case StateFilter(state=s, value=v):
                if s == "enabled":
                    # enabled is inverse of disabled
                    return [c for c in candidates if (not c.get("disabled", False)) == v]
                return [c for c in candidates if c.get(s, False) == v]

            case AndExpr(children=children):
                result = candidates
                for child in children:
                    result = self._apply(result, child)
                return result

            case OrExpr(children=children):
                results = set()
                for child in children:
                    for match in self._apply(candidates, child):
                        results.add(id(match))
                return [c for c in candidates if id(c) in results]

            case WithinExpr(container=container, inner=inner):
                # Find container nodes
                containers = self._apply(candidates, container)
                # Find all descendants of containers
                container_descendants: set[int] = set()
                for c in containers:
                    self._collect_descendants(c, container_descendants)
                # Apply inner query to descendants only
                return self._apply([n for n in candidates if id(n) in container_descendants], inner)

            case NearExpr(anchor=anchor, inner=inner, max_distance=max_dist):
                # Find anchor nodes
                anchors = self._apply(candidates, anchor)
                # Find inner matches
                inner_matches = self._apply(candidates, inner)
                # Filter by proximity using bbox
                result = []
                for match in inner_matches:
                    match_bbox = match.get("_bbox")
                    if not match_bbox:
                        continue
                    for anc in anchors:
                        anc_bbox = anc.get("_bbox")
                        if anc_bbox and match_bbox.distance_to(anc_bbox) <= max_dist:
                            result.append(match)
                            break
                return result

            case NthExpr(index=idx, inner=inner):
                matches = self._apply(candidates, inner)
                if 0 <= idx < len(matches):
                    return [matches[idx]]
                return []

        return candidates

    def _collect_descendants(self, node: dict[str, Any], result: set[int]) -> None:
        """Collect all descendant node IDs."""
        for child in node.get("children", []):
            result.add(id(child))
            self._collect_descendants(child, result)
