"""
AST node definitions for the query language.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleFilter:
    """Filter by ARIA role."""

    role: str


@dataclass(frozen=True)
class NameFilter:
    """Filter by accessible name."""

    pattern: str
    is_regex: bool


@dataclass(frozen=True)
class TextFilter:
    """Filter by text content."""

    pattern: str
    is_regex: bool


@dataclass(frozen=True)
class IdFilter:
    """Filter by element ID."""

    id: str


@dataclass(frozen=True)
class StateFilter:
    """Filter by element state."""

    state: str  # enabled, checked, expanded, required
    value: bool


@dataclass(frozen=True)
class WithinExpr:
    """Filter elements that are within a container."""

    container: "QueryExpr"
    inner: "QueryExpr"


@dataclass(frozen=True)
class NearExpr:
    """Filter elements that are near an anchor element."""

    anchor: "QueryExpr"
    inner: "QueryExpr"
    max_distance: int = 100  # pixels


@dataclass(frozen=True)
class NthExpr:
    """Select the nth match."""

    index: int
    inner: "QueryExpr"


@dataclass(frozen=True)
class AndExpr:
    """Logical AND of multiple filters."""

    children: tuple["QueryExpr", ...]


@dataclass(frozen=True)
class OrExpr:
    """Logical OR of multiple filters."""

    children: tuple["QueryExpr", ...]


QueryExpr = (
    RoleFilter
    | NameFilter
    | TextFilter
    | IdFilter
    | StateFilter
    | WithinExpr
    | NearExpr
    | NthExpr
    | AndExpr
    | OrExpr
)
