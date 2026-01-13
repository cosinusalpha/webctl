"""Command handlers for webctl daemon."""

# Import handlers to register them (side effect: registers handlers)
from . import console, hitl, interact, navigation, observe, session, wait  # noqa: F401
from .registry import get_handler, list_handlers, register

__all__ = [
    "register",
    "get_handler",
    "list_handlers",
]
