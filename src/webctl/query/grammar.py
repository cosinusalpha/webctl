"""
Query language grammar definition.

The query language allows targeting elements in the accessibility tree
using filters and combinators.

Examples:
    role=button                     - All buttons
    role=button name="Submit"       - Button with exact name
    role=textbox name~="email"      - Textbox with name containing "email"
    within(role=form) role=button   - Buttons inside forms
    role=button nth=0               - First button
"""

QUERY_GRAMMAR = r"""
    start: expr

    expr: or_expr

    or_expr: and_expr ("|" and_expr)*
    and_expr: atom+

    atom: filter
        | "within" "(" expr ")"         -> within_expr
        | "near" "(" expr ")"           -> near_expr
        | "nth" "=" INT                 -> nth_expr
        | "(" expr ")"                  -> group

    filter: "role" "=" IDENTIFIER       -> role_filter
          | "name" "~=" STRING          -> name_regex_filter
          | "name" "=" STRING           -> name_exact_filter
          | "text" "~=" STRING          -> text_regex_filter
          | "text" "=" STRING           -> text_exact_filter
          | "id" "=" IDENTIFIER         -> id_filter
          | "enabled" "=" BOOL          -> enabled_filter
          | "checked" "=" BOOL          -> checked_filter
          | "expanded" "=" BOOL         -> expanded_filter
          | "required" "=" BOOL         -> required_filter

    IDENTIFIER: /[a-zA-Z][a-zA-Z0-9_-]*/
    STRING: /"[^"]*"/
    BOOL: "true" | "false"
    INT: /[0-9]+/

    %import common.WS
    %ignore WS
"""
