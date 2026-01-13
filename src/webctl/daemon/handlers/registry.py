"""
Handler registry for command dispatch.
"""

from collections.abc import AsyncIterator, Callable

from ...protocol.messages import Response

HandlerFunc = Callable[..., AsyncIterator[Response]]

_handlers: dict[str, HandlerFunc] = {}


def register(command: str) -> Callable[[HandlerFunc], HandlerFunc]:
    """Decorator to register a command handler."""

    def decorator(func: HandlerFunc) -> HandlerFunc:
        _handlers[command] = func
        return func

    return decorator


def get_handler(command: str) -> HandlerFunc | None:
    """Get handler for a command."""
    return _handlers.get(command)


def list_handlers() -> list[str]:
    """List all registered commands."""
    return list(_handlers.keys())
