"""
Action execution for Consent-O-Matic rules.

Implements the various action types supported by Consent-O-Matic:
- click: Click an element
- hide: Hide an element by adding a CSS class
- wait: Wait for a specified time
- waitcss: Wait for a CSS selector to appear/disappear
- ifcss: Conditional action based on CSS selector
- foreach: Iterate over matched elements
- list: Execute a sequence of actions
- consent: Apply consent based on preferences
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)

# Default timeout for actions (ms)
ACTION_TIMEOUT = 3000
WAIT_TIMEOUT = 5000


class TargetResolver:
    """Resolve Consent-O-Matic target specifications to Playwright locators."""

    async def resolve(
        self,
        target: dict[str, Any] | str,
        context: Page | Locator,
        multiple: bool = False,
    ) -> Locator | None:
        """
        Resolve a target specification to a Playwright locator.

        Args:
            target: Target specification (selector string or dict with options)
            context: Page or Locator to search within
            multiple: If True, return all matches; if False, return first match

        Returns:
            Locator or None if not found
        """
        if isinstance(target, str):
            target = {"selector": target}

        selector = target.get("selector")
        if not selector:
            return None

        try:
            # Start with the base selector
            locator = context.locator(selector)

            # Apply text filter if specified
            text_filter = target.get("textFilter")
            if text_filter:
                locator = locator.filter(has_text=text_filter)

            # Apply display filter if specified (visibility check)
            display_filter = target.get("displayFilter")
            if display_filter is not None:
                if display_filter:
                    # Must be visible
                    locator = locator.locator("visible=true")

            # Handle parent traversal
            parent = target.get("parent")
            if parent:
                parent_locator = await self._resolve_parent(locator, parent, context)
                if parent_locator is None:
                    return None
                locator = parent_locator

            # Check if any elements match
            count = await locator.count()
            if count == 0:
                return None

            return locator if multiple else locator.first

        except Exception as e:
            logger.debug("Failed to resolve target %s: %s", selector, e)
            return None

    async def _resolve_parent(
        self,
        locator: Locator,
        parent: dict[str, Any],
        root_context: Page | Locator,
    ) -> Locator | None:
        """Resolve parent traversal specification."""
        # Get the selector for parent matching
        parent_selector = parent.get("selector")
        if not parent_selector:
            return None

        # Get child filter if specified
        child_filter = parent.get("childFilter")

        try:
            # Find the original element first to get its position
            if await locator.count() == 0:
                return None

            # We need to use JavaScript to traverse up to the parent
            # This is a simplified implementation that uses CSS :has() where possible
            original_selector = await locator.evaluate(
                """
                el => {
                    // Generate a unique selector for this element
                    let selector = el.tagName.toLowerCase();
                    if (el.id) selector += '#' + el.id;
                    else if (el.className) selector += '.' + el.className.split(' ').join('.');
                    return selector;
                }
            """
            )

            # Try to find parent matching the parent selector that contains our element
            parent_locator = root_context.locator(
                f"{parent_selector}:has({original_selector})"
            )

            if await parent_locator.count() > 0:
                # Apply child filter if specified
                if child_filter:
                    child_selector = child_filter.get("selector")
                    if child_selector:
                        parent_locator = parent_locator.filter(
                            has=root_context.locator(child_selector)
                        )

                if await parent_locator.count() > 0:
                    return parent_locator.first

            return None

        except Exception as e:
            logger.debug("Failed to resolve parent: %s", e)
            return None


class ActionExecutor:
    """Execute Consent-O-Matic actions on a page."""

    def __init__(self, accept_all: bool = True) -> None:
        """
        Initialize the action executor.

        Args:
            accept_all: If True, always accept all cookies (simplified implementation)
        """
        self.accept_all = accept_all
        self._target_resolver = TargetResolver()

    async def execute(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """
        Execute an action on the page.

        Args:
            action: Action specification
            context: Page or Locator to execute action on

        Returns:
            True if action executed successfully
        """
        if not action:
            return True

        action_type = action.get("type", "")

        handlers = {
            "click": self._execute_click,
            "hide": self._execute_hide,
            "wait": self._execute_wait,
            "waitcss": self._execute_waitcss,
            "ifcss": self._execute_ifcss,
            "foreach": self._execute_foreach,
            "list": self._execute_list,
            "consent": self._execute_consent,
            "close": self._execute_noop,  # We don't open new tabs
            "slide": self._execute_noop,  # Rare, defer to fallback
        }

        handler = handlers.get(action_type)
        if handler is None:
            logger.debug("Unknown action type: %s", action_type)
            return False

        try:
            return await handler(action, context)
        except Exception as e:
            logger.debug("Action %s failed: %s", action_type, e)
            return False

    async def _execute_click(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """Execute a click action."""
        target = action.get("target")
        if not target:
            return False

        locator = await self._target_resolver.resolve(target, context)
        if locator is None:
            return False

        try:
            # Check if element is visible and clickable
            if not await locator.is_visible():
                return False

            await locator.click(timeout=ACTION_TIMEOUT)
            # Small delay after click for page to process
            await asyncio.sleep(0.3)
            return True
        except Exception as e:
            logger.debug("Click failed: %s", e)
            return False

    async def _execute_hide(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """Execute a hide action (add CSS class to hide element)."""
        target = action.get("target")
        if not target:
            return False

        locator = await self._target_resolver.resolve(target, context, multiple=True)
        if locator is None:
            return False

        try:
            # Add a class to hide the elements
            await locator.evaluate_all(
                """
                elements => elements.forEach(el => {
                    el.style.setProperty('display', 'none', 'important');
                })
            """
            )
            return True
        except Exception as e:
            logger.debug("Hide failed: %s", e)
            return False

    async def _execute_wait(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """Execute a wait action (sleep for specified time)."""
        wait_time = action.get("waitTime", 0)
        if wait_time > 0:
            # Cap wait time to prevent very long waits
            wait_time = min(wait_time, 5000)
            await asyncio.sleep(wait_time / 1000)
        return True

    async def _execute_waitcss(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """Execute a waitcss action (wait for selector to appear/disappear)."""
        target = action.get("target")
        if not target:
            return False

        selector = target.get("selector") if isinstance(target, dict) else target
        if not selector:
            return False

        retries = action.get("retries", 10)
        wait_time = action.get("waitTime", 250)
        negate = action.get("negated", False)

        try:
            for _ in range(retries):
                locator = context.locator(selector)
                count = await locator.count()
                exists = count > 0

                if (negate and not exists) or (not negate and exists):
                    return True

                await asyncio.sleep(wait_time / 1000)

            return False
        except Exception as e:
            logger.debug("Waitcss failed: %s", e)
            return False

    async def _execute_ifcss(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """Execute an ifcss action (conditional based on CSS selector)."""
        target = action.get("target")
        if not target:
            return False

        locator = await self._target_resolver.resolve(target, context)
        condition_met = locator is not None

        if condition_met:
            true_action = action.get("trueAction")
            if true_action:
                return await self.execute(true_action, context)
            return True
        else:
            false_action = action.get("falseAction")
            if false_action:
                return await self.execute(false_action, context)
            return True

    async def _execute_foreach(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """Execute a foreach action (iterate over matched elements)."""
        target = action.get("target")
        if not target:
            return False

        locator = await self._target_resolver.resolve(target, context, multiple=True)
        if locator is None:
            return True  # No elements to iterate over is success

        sub_action = action.get("action")
        if not sub_action:
            return True

        try:
            count = await locator.count()
            for i in range(count):
                element = locator.nth(i)
                await self.execute(sub_action, element)
            return True
        except Exception as e:
            logger.debug("Foreach failed: %s", e)
            return False

    async def _execute_list(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """Execute a list action (sequence of actions)."""
        actions = action.get("actions", [])
        for sub_action in actions:
            success = await self.execute(sub_action, context)
            if not success:
                # Continue trying other actions even if one fails
                logger.debug("Sub-action failed, continuing")
        return True

    async def _execute_consent(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """
        Execute a consent action.

        For "accept all" policy, we execute trueAction for all consent types.
        """
        consents = action.get("consents", [])

        for consent in consents:
            # For accept_all mode, always execute the "accept" path (trueAction)
            if self.accept_all:
                true_action = consent.get("trueAction")
                if true_action:
                    await self.execute(true_action, context)
                else:
                    # If no trueAction, try toggleAction (to enable)
                    toggle_action = consent.get("toggleAction")
                    if toggle_action:
                        # Check if already enabled
                        matcher = consent.get("matcher")
                        if matcher:
                            target = matcher.get("target")
                            if target:
                                locator = await self._target_resolver.resolve(
                                    target, context
                                )
                                if locator is None:
                                    # Not already enabled, toggle it on
                                    await self.execute(toggle_action, context)
            else:
                # For reject mode, execute falseAction or toggle off
                false_action = consent.get("falseAction")
                if false_action:
                    await self.execute(false_action, context)
                else:
                    toggle_action = consent.get("toggleAction")
                    if toggle_action:
                        # Check if enabled, toggle it off
                        matcher = consent.get("matcher")
                        if matcher:
                            target = matcher.get("target")
                            if target:
                                locator = await self._target_resolver.resolve(
                                    target, context
                                )
                                if locator is not None:
                                    # Currently enabled, toggle it off
                                    await self.execute(toggle_action, context)

        return True

    async def _execute_noop(
        self,
        action: dict[str, Any],
        context: Page | Locator,
    ) -> bool:
        """No-op action for unsupported action types."""
        return True
