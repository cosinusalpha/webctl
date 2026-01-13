"""
Async event broadcasting for RFC SS11 events.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from ..protocol.messages import EventResponse, EventType

EventCallback = Callable[[EventResponse], Awaitable[None]]


class EventEmitter:
    """Broadcast events to all connected clients."""

    def __init__(self) -> None:
        self._subscribers: list[EventCallback] = []
        self._event_queue: asyncio.Queue[EventResponse] = asyncio.Queue()
        self._running = False
        self._broadcast_task: asyncio.Task[None] | None = None

    def subscribe(self, callback: EventCallback) -> None:
        """Subscribe to events."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        """Unsubscribe from events."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        """Emit an event to all subscribers."""
        response = EventResponse(event=event, payload=payload)
        await self._event_queue.put(response)

    async def start(self) -> None:
        """Start the event broadcast loop."""
        self._running = True
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        """Stop the event broadcast loop."""
        self._running = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task

    async def _broadcast_loop(self) -> None:
        """Continuously broadcast queued events."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)

                # Broadcast to all subscribers
                for callback in self._subscribers:
                    try:
                        await callback(event)
                    except Exception:
                        pass  # Don't let one subscriber break others

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                break

    # Convenience methods for RFC SS11 events

    async def emit_navigation_started(self, url: str, page_id: str | None = None) -> None:
        await self.emit(EventType.NAVIGATION_STARTED, {"url": url, "page_id": page_id})

    async def emit_navigation_finished(self, url: str, page_id: str | None = None) -> None:
        await self.emit(EventType.NAVIGATION_FINISHED, {"url": url, "page_id": page_id})

    async def emit_page_opened(self, page_id: str, url: str, kind: str) -> None:
        await self.emit(EventType.PAGE_OPENED, {"page_id": page_id, "url": url, "kind": kind})

    async def emit_page_focused(self, page_id: str, url: str) -> None:
        await self.emit(EventType.PAGE_FOCUSED, {"page_id": page_id, "url": url})

    async def emit_page_closed(self, page_id: str) -> None:
        await self.emit(EventType.PAGE_CLOSED, {"page_id": page_id})

    async def emit_view_changed(
        self,
        page_id: str,
        view: str,
        change_type: str,
        changed_count: int | None = None,
    ) -> None:
        await self.emit(
            EventType.VIEW_CHANGED,
            {
                "page_id": page_id,
                "view": view,
                "change_type": change_type,
                "changed_count": changed_count,
            },
        )

    async def emit_auth_required(
        self, page_id: str, kind: str, provider: str | None, url: str
    ) -> None:
        await self.emit(
            EventType.AUTH_REQUIRED,
            {
                "page_id": page_id,
                "kind": kind,
                "provider": provider,
                "url": url,
                "requires_interaction": True,
            },
        )

    async def emit_user_action_required(
        self,
        page_id: str,
        kind: str,
        description: str,
        selector_hint: str | None = None,
    ) -> None:
        await self.emit(
            EventType.USER_ACTION_REQUIRED,
            {
                "page_id": page_id,
                "kind": kind,
                "description": description,
                "selector_hint": selector_hint,
            },
        )
