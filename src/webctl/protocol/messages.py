"""
Protocol message definitions.

RFC-compliant message types for client-daemon communication.
"""

import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    REQUEST = "request"
    ITEM = "item"
    EVENT = "event"
    ERROR = "error"
    DONE = "done"


# === Requests ===


class Request(BaseModel):
    """Client -> Daemon request."""

    req_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    command: str
    args: dict[str, Any] = Field(default_factory=dict)


# === Responses ===


class ItemResponse(BaseModel):
    """Data item (e.g., a11y node, markdown content)."""

    type: Literal["item"] = "item"
    req_id: str
    view: str
    data: dict[str, Any]


class EventResponse(BaseModel):
    """Async event notification."""

    type: Literal["event"] = "event"
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Error response."""

    type: Literal["error"] = "error"
    req_id: str | None = None
    error: str
    code: str | None = None
    details: dict[str, Any] | None = None


class DoneResponse(BaseModel):
    """Terminal response indicating command completion."""

    type: Literal["done"] = "done"
    req_id: str
    ok: bool
    summary: dict[str, Any] | None = None


Response = ItemResponse | EventResponse | ErrorResponse | DoneResponse


# === Event Types (RFC SS11) ===


class EventType(str, Enum):
    # Navigation events
    NAVIGATION_STARTED = "navigation.started"
    NAVIGATION_FINISHED = "navigation.finished"

    # Page events
    PAGE_OPENED = "page.opened"
    PAGE_FOCUSED = "page.focused"
    PAGE_CLOSED = "page.closed"

    # View events
    VIEW_CHANGED = "view.changed"

    # Human-in-the-loop events
    AUTH_REQUIRED = "auth.required"
    USER_ACTION_REQUIRED = "user.action.required"


# === Event Payloads ===


class NavigationEventPayload(BaseModel):
    url: str
    page_id: str | None = None


class PageEventPayload(BaseModel):
    page_id: str
    url: str
    kind: Literal["tab", "popup"]


class ViewChangedPayload(BaseModel):
    page_id: str
    view: str  # "a11y" | "md" | "dom-lite"
    change_type: Literal["added", "removed", "modified", "major"]
    changed_count: int | None = None


class AuthRequiredPayload(BaseModel):
    """RFC SS11: auth.required event payload."""

    page_id: str
    kind: Literal["sso", "mfa", "oauth", "login", "unknown"]
    provider: str | None = None  # e.g., "google", "microsoft", "okta"
    url: str
    requires_interaction: bool = True


class UserActionRequiredPayload(BaseModel):
    """RFC SS11: user.action.required event payload."""

    page_id: str
    kind: Literal["captcha", "cookie_consent", "terms", "rate_limit", "unknown"]
    description: str
    selector_hint: str | None = None  # CSS selector if detectable
