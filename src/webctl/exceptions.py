"""
Custom exceptions for webctl.
"""

from typing import Any


class WebctlError(Exception):
    """Base exception for webctl."""

    pass


class ConnectionError(WebctlError):
    """Failed to connect to daemon."""

    pass


class SessionError(WebctlError):
    """Session-related error."""

    pass


class SessionNotFoundError(SessionError):
    """Session does not exist."""

    pass


class QueryError(WebctlError):
    """Query-related error."""

    pass


class ParseError(QueryError):
    """Failed to parse query."""

    pass


class NoMatchError(QueryError):
    """No elements matched the query."""

    pass


class AmbiguousTargetError(QueryError):
    """Multiple elements matched when only one expected."""

    def __init__(self, message: str, matches: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.matches = matches or []


class NavigationError(WebctlError):
    """Navigation failed."""

    pass


class DomainBlockedError(NavigationError):
    """Domain is blocked by policy."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"Navigation blocked: {reason}")
        self.url = url
        self.reason = reason


class TimeoutError(WebctlError):
    """Operation timed out."""

    pass


class InteractionError(WebctlError):
    """Failed to interact with element."""

    pass


class ElementNotInteractableError(InteractionError):
    """Element is not interactable."""

    pass


class ViewError(WebctlError):
    """View extraction error."""

    pass


class DaemonError(WebctlError):
    """Daemon-related error."""

    pass


class DaemonNotRunningError(DaemonError):
    """Daemon is not running."""

    pass


class AuthRequiredError(WebctlError):
    """Authentication required for this action."""

    def __init__(self, kind: str, provider: str | None = None, url: str | None = None):
        message = f"Authentication required: {kind}"
        if provider:
            message += f" ({provider})"
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.url = url
