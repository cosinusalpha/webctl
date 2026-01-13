"""
Query parser using Lark.
"""

from lark import Lark, Token, Transformer, v_args

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
from .grammar import QUERY_GRAMMAR

# Type alias for modifier tuples used internally during parsing
_Modifier = tuple[str, QueryExpr | int]

# Type alias for items that can appear during transformation
_TransformItem = QueryExpr | _Modifier


class QueryTransformer(Transformer[Token, QueryExpr]):
    """Transform parse tree into AST."""

    @v_args(inline=True)
    def role_filter(self, identifier: Token) -> RoleFilter:
        return RoleFilter(role=str(identifier))

    @v_args(inline=True)
    def name_regex_filter(self, regex: Token) -> NameFilter:
        return NameFilter(pattern=str(regex)[1:-1], is_regex=True)

    @v_args(inline=True)
    def name_exact_filter(self, string: Token) -> NameFilter:
        return NameFilter(pattern=str(string)[1:-1], is_regex=False)

    @v_args(inline=True)
    def text_regex_filter(self, regex: Token) -> TextFilter:
        return TextFilter(pattern=str(regex)[1:-1], is_regex=True)

    @v_args(inline=True)
    def text_exact_filter(self, string: Token) -> TextFilter:
        return TextFilter(pattern=str(string)[1:-1], is_regex=False)

    @v_args(inline=True)
    def id_filter(self, identifier: Token) -> IdFilter:
        return IdFilter(id=str(identifier))

    @v_args(inline=True)
    def enabled_filter(self, value: Token) -> StateFilter:
        return StateFilter(state="enabled", value=str(value) == "true")

    @v_args(inline=True)
    def checked_filter(self, value: Token) -> StateFilter:
        return StateFilter(state="checked", value=str(value) == "true")

    @v_args(inline=True)
    def expanded_filter(self, value: Token) -> StateFilter:
        return StateFilter(state="expanded", value=str(value) == "true")

    @v_args(inline=True)
    def required_filter(self, value: Token) -> StateFilter:
        return StateFilter(state="required", value=str(value) == "true")

    @v_args(inline=True)
    def within_expr(self, inner: QueryExpr) -> _Modifier:
        return ("within", inner)

    @v_args(inline=True)
    def near_expr(self, inner: QueryExpr) -> _Modifier:
        return ("near", inner)

    @v_args(inline=True)
    def nth_expr(self, index: Token) -> _Modifier:
        return ("nth", int(index))

    def group(self, items: list[QueryExpr]) -> QueryExpr:
        return items[0]

    def filter(self, items: list[QueryExpr]) -> QueryExpr:
        return items[0]

    def atom(self, items: list[_TransformItem]) -> _TransformItem:
        return items[0]

    def expr(self, items: list[QueryExpr]) -> QueryExpr:
        return items[0]

    def and_expr(self, items: list[_TransformItem]) -> QueryExpr:
        # Handle within/near/nth as modifiers
        filters: list[QueryExpr] = []
        modifiers: list[_Modifier] = []
        for item in items:
            if isinstance(item, tuple):
                modifiers.append(item)
            else:
                filters.append(item)

        result: QueryExpr = filters[0] if len(filters) == 1 else AndExpr(children=tuple(filters))

        # Apply modifiers (in reverse order for proper nesting)
        for mod_type, mod_value in reversed(modifiers):
            if mod_type == "within":
                assert not isinstance(mod_value, int)
                result = WithinExpr(container=mod_value, inner=result)
            elif mod_type == "near":
                assert not isinstance(mod_value, int)
                result = NearExpr(anchor=mod_value, inner=result)
            elif mod_type == "nth":
                assert isinstance(mod_value, int)
                result = NthExpr(index=mod_value, inner=result)

        return result

    def or_expr(self, items: list[QueryExpr]) -> QueryExpr:
        if len(items) == 1:
            return items[0]
        return OrExpr(children=tuple(items))

    def start(self, items: list[QueryExpr]) -> QueryExpr:
        return items[0]


_parser = Lark(QUERY_GRAMMAR, parser="lalr", transformer=QueryTransformer())


def parse_query(query: str) -> QueryExpr:
    """Parse query string into AST."""
    return _parser.parse(query)  # type: ignore[return-value]
