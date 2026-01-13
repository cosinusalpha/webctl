"""
RFC SS11: view.changed event

Detect when the accessibility tree or page content changes significantly.
Used for:
- Reactive agent patterns
- wait --until view-changed:a11y
"""

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import Page


@dataclass
class ViewChangeEvent:
    """Event emitted when a view changes."""

    page_id: str
    view: str
    change_type: Literal["added", "removed", "modified", "major"]
    old_hash: str
    new_hash: str
    changed_count: int | None = None


class ViewChangeDetector:
    """Monitors page for accessibility tree changes."""

    def __init__(
        self,
        page: Page,
        page_id: str,
        callback: Callable[[ViewChangeEvent], Awaitable[None]],
        debounce_ms: int = 500,
    ):
        self.page = page
        self.page_id = page_id
        self.callback = callback
        self.debounce_ms = debounce_ms

        self._last_hash: str | None = None
        self._last_snapshot: dict[str, Any] | None = None
        self._monitoring = False
        self._poll_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start monitoring for view changes."""
        self._monitoring = True

        # Get initial snapshot
        self._last_hash, self._last_snapshot = await self._get_snapshot_hash()

        # Set up mutation observer via page
        with contextlib.suppress(Exception):
            await self.page.evaluate(
                """
                () => {
                    if (window.__webctl_observer) {
                        window.__webctl_observer.disconnect();
                    }

                    window.__webctl_mutation_pending = false;

                    window.__webctl_observer = new MutationObserver((mutations) => {
                        // Filter out trivial mutations
                        const dominated = mutations.some(m =>
                            m.type === 'childList' ||
                            (m.type === 'attributes' &&
                             ['class', 'style', 'hidden', 'disabled', 'aria-hidden'].includes(m.attributeName))
                        );

                        if (dominated) {
                            window.__webctl_mutation_pending = true;
                        }
                    });

                    window.__webctl_observer.observe(document.body, {
                        childList: true,
                        subtree: true,
                        attributes: true,
                        attributeFilter: ['class', 'style', 'hidden', 'disabled', 'aria-hidden', 'aria-expanded']
                    });
                }
            """
            )

        # Start polling for mutations
        self._poll_task = asyncio.create_task(self._poll_mutations())

    async def stop(self) -> None:
        """Stop monitoring."""
        self._monitoring = False

        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task

        with contextlib.suppress(Exception):
            await self.page.evaluate(
                """
                () => {
                    if (window.__webctl_observer) {
                        window.__webctl_observer.disconnect();
                        window.__webctl_observer = null;
                    }
                }
            """
            )

    async def _poll_mutations(self) -> None:
        """Poll for pending mutations and check for changes."""
        while self._monitoring:
            await asyncio.sleep(self.debounce_ms / 1000)

            try:
                has_mutations = await self.page.evaluate(
                    """
                    () => {
                        const pending = window.__webctl_mutation_pending;
                        window.__webctl_mutation_pending = false;
                        return pending;
                    }
                """
                )

                if has_mutations:
                    await self._check_for_changes()

            except Exception:
                # Page might be navigating or closed
                pass

    async def _check_for_changes(self) -> None:
        """Check if a11y tree has actually changed."""
        new_hash, new_snapshot = await self._get_snapshot_hash()

        if new_hash != self._last_hash:
            # Determine change type
            change_type = self._classify_change(self._last_snapshot, new_snapshot)
            changed_count = self._count_changes(self._last_snapshot, new_snapshot)

            event = ViewChangeEvent(
                page_id=self.page_id,
                view="a11y",
                change_type=change_type,
                old_hash=self._last_hash or "",
                new_hash=new_hash,
                changed_count=changed_count,
            )

            self._last_hash = new_hash
            self._last_snapshot = new_snapshot

            await self.callback(event)

    async def _get_snapshot_hash(self) -> tuple[str, dict[str, Any] | None]:
        """Get hash of current a11y snapshot."""
        try:
            snapshot_str = await self.page.locator("body").aria_snapshot()
            if snapshot_str:
                hash_val = hashlib.sha256(snapshot_str.encode()).hexdigest()[:16]
                return hash_val, {"raw": snapshot_str}
        except Exception:
            pass
        return "", None

    def _classify_change(
        self, old: dict[str, Any] | None, new: dict[str, Any] | None
    ) -> Literal["added", "removed", "modified", "major"]:
        """Classify the type of change."""
        if old is None and new is not None:
            return "added"
        if old is not None and new is None:
            return "removed"

        if old and new:
            old_count = self._count_nodes(old)
            new_count = self._count_nodes(new)

            diff = abs(new_count - old_count)
            if diff > old_count * 0.3:  # More than 30% change
                return "major"

        return "modified"

    def _count_nodes(self, tree: dict[str, Any]) -> int:
        """Count nodes in a11y tree."""
        count = 1
        for child in tree.get("children", []):
            count += self._count_nodes(child)
        return count

    def _count_changes(self, old: dict[str, Any] | None, new: dict[str, Any] | None) -> int | None:
        """Estimate number of changed nodes."""
        if not old or not new:
            return None

        old_count = self._count_nodes(old)
        new_count = self._count_nodes(new)
        return abs(new_count - old_count)


async def wait_for_view_change(page: Page, timeout_ms: int = 30000, view: str = "a11y") -> bool:
    """
    Wait until the view changes.
    Used for: wait --until view-changed:a11y
    """
    changed = asyncio.Event()

    async def on_change(event: ViewChangeEvent) -> None:
        changed.set()

    detector = ViewChangeDetector(page, "temp", on_change)
    await detector.start()

    try:
        await asyncio.wait_for(changed.wait(), timeout=timeout_ms / 1000)
        return True
    except TimeoutError:
        return False
    finally:
        await detector.stop()
