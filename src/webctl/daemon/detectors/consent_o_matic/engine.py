"""
Consent-O-Matic engine for cookie banner detection and handling.

This is the main orchestrator that coordinates CMP detection and rule execution.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .actions import ActionExecutor, TargetResolver
from .rules import get_rules_manager

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Timeout for CMP detection (ms)
DETECTION_TIMEOUT = 2000
# Timeout for overall consent handling (ms)
CONSENT_TIMEOUT = 10000


@dataclass
class ConsentOMaticResult:
    """Result of Consent-O-Matic handling."""

    handled: bool = False
    cmp_name: str | None = None
    methods_executed: list[str] = field(default_factory=list)
    error: str | None = None


class ConsentOMaticEngine:
    """Main engine for Consent-O-Matic cookie banner handling."""

    def __init__(self, accept_all: bool = True) -> None:
        """
        Initialize the engine.

        Args:
            accept_all: If True, accept all cookies for fastest dismissal
        """
        self.accept_all = accept_all
        self._rules_manager = get_rules_manager()
        self._action_executor = ActionExecutor(accept_all=accept_all)
        self._target_resolver = TargetResolver()

    async def detect_and_handle(self, page: Page) -> ConsentOMaticResult:
        """
        Detect and handle cookie consent banner using Consent-O-Matic rules.

        Args:
            page: Playwright page to handle

        Returns:
            Result indicating whether a CMP was detected and handled
        """
        result = ConsentOMaticResult()

        try:
            # Load rules
            raw_rules = await self._rules_manager.get_raw_rules()
            if not raw_rules:
                result.error = "No rules loaded"
                return result

            # Detect which CMP is present
            cmp_name = await self._detect_cmp(page, raw_rules)
            if not cmp_name:
                return result  # No CMP detected

            result.cmp_name = cmp_name
            logger.debug("Detected CMP: %s", cmp_name)

            # Get the rule for this CMP
            cmp_rule = raw_rules.get(cmp_name)
            if not cmp_rule:
                result.error = f"No rule found for CMP: {cmp_name}"
                return result

            # Check if popup is showing
            popup_showing = await self._detect_popup(page, cmp_rule)
            if not popup_showing:
                logger.debug("CMP detected but popup not showing")
                return result

            # Execute consent handling methods
            handled = await self._execute_consent_methods(page, cmp_rule, result)
            result.handled = handled

            return result

        except TimeoutError:
            result.error = "Timeout during consent handling"
            return result
        except Exception as e:
            result.error = str(e)
            logger.debug("Error during consent handling: %s", e)
            return result

    async def _detect_cmp(
        self, page: Page, raw_rules: dict[str, Any]
    ) -> str | None:
        """Detect which CMP is present on the page."""
        # Check each CMP's detection rules
        for cmp_name, cmp_rule in raw_rules.items():
            try:
                detectors = cmp_rule.get("detectors", [])
                for detector in detectors:
                    present_matcher = detector.get("presentMatcher")
                    if present_matcher and await self._check_matcher(
                        page, present_matcher
                    ):
                        return cmp_name
            except Exception as e:
                logger.debug("Error checking CMP %s: %s", cmp_name, e)
                continue

        return None

    async def _detect_popup(self, page: Page, cmp_rule: dict[str, Any]) -> bool:
        """Check if the CMP popup is currently showing."""
        detectors = cmp_rule.get("detectors", [])
        for detector in detectors:
            showing_matcher = detector.get("showingMatcher")
            if showing_matcher:
                try:
                    if await self._check_matcher(page, showing_matcher):
                        return True
                except Exception:
                    continue
        return False

    async def _check_matcher(self, page: Page, matcher: dict[str, Any]) -> bool:
        """Check if a matcher condition is satisfied."""
        matcher_type = matcher.get("type", "")

        if matcher_type == "css":
            return await self._check_css_matcher(page, matcher)
        elif matcher_type == "checkbox":
            return await self._check_checkbox_matcher(page, matcher)

        return False

    async def _check_css_matcher(self, page: Page, matcher: dict[str, Any]) -> bool:
        """Check if a CSS selector matches."""
        target = matcher.get("target")
        if not target:
            return False

        locator = await self._target_resolver.resolve(target, page)
        if locator is None:
            return False

        # Check display filter if specified
        display_filter = target.get("displayFilter") if isinstance(target, dict) else None
        if display_filter is not None and display_filter:
            try:
                is_visible = await locator.is_visible()
                if not is_visible:
                    return False
            except Exception:
                return False

        return True

    async def _check_checkbox_matcher(
        self, page: Page, matcher: dict[str, Any]
    ) -> bool:
        """Check if a checkbox is checked."""
        target = matcher.get("target")
        if not target:
            return False

        locator = await self._target_resolver.resolve(target, page)
        if locator is None:
            return False

        try:
            return await locator.is_checked()
        except Exception:
            return False

    async def _execute_consent_methods(
        self, page: Page, cmp_rule: dict[str, Any], result: ConsentOMaticResult
    ) -> bool:
        """Execute the consent handling methods for the CMP."""
        methods = cmp_rule.get("methods", [])
        if not methods:
            return False

        # Build method lookup
        method_map: dict[str, dict[str, Any]] = {}
        for method in methods:
            name = method.get("name", "")
            if name:
                method_map[name] = method

        # For "accept all" mode, we follow this simplified order:
        # 1. Try SAVE_CONSENT first (many CMPs have a direct "Accept All" button)
        # 2. If that doesn't work, try: OPEN_OPTIONS -> DO_CONSENT -> SAVE_CONSENT
        # 3. Optionally HIDE_CMP at the end

        if self.accept_all:
            # First, try direct accept (SAVE_CONSENT might just be "Accept All" button)
            save_consent = method_map.get("SAVE_CONSENT")
            if save_consent:
                action = save_consent.get("action")
                if action:
                    success = await self._action_executor.execute(action, page)
                    if success:
                        result.methods_executed.append("SAVE_CONSENT")
                        # Check if popup is gone
                        await asyncio.sleep(0.5)
                        if not await self._detect_popup(page, cmp_rule):
                            return True

            # If direct accept didn't work, try the full flow
            method_order = ["OPEN_OPTIONS", "DO_CONSENT", "SAVE_CONSENT", "HIDE_CMP"]
        else:
            # For reject mode (not currently used, but supported)
            method_order = ["OPEN_OPTIONS", "DO_CONSENT", "SAVE_CONSENT", "HIDE_CMP"]

        for method_name in method_order:
            method = method_map.get(method_name)
            if not method:
                continue

            action = method.get("action")
            if not action:
                continue

            try:
                success = await self._action_executor.execute(action, page)
                if success:
                    result.methods_executed.append(method_name)
                    # Small delay between method executions
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug("Method %s failed: %s", method_name, e)

        # Check if we successfully handled the consent
        # Give a moment for any animations to complete
        await asyncio.sleep(0.5)

        # Verify popup is gone
        if not await self._detect_popup(page, cmp_rule):
            return True

        # Try hide as last resort
        hide_method = method_map.get("HIDE_CMP")
        if hide_method and "HIDE_CMP" not in result.methods_executed:
            action = hide_method.get("action")
            if action:
                await self._action_executor.execute(action, page)
                result.methods_executed.append("HIDE_CMP")
                return True

        return len(result.methods_executed) > 0
