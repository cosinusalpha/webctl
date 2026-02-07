"""Unit tests for Consent-O-Matic integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRulesManager:
    """Tests for the RulesManager class."""

    @pytest.fixture
    def sample_rules(self) -> dict[str, Any]:
        """Sample Consent-O-Matic rules for testing."""
        return {
            "TestCMP": {
                "detectors": [
                    {
                        "presentMatcher": {
                            "type": "css",
                            "target": {"selector": "#test-consent-banner"},
                        },
                        "showingMatcher": {
                            "type": "css",
                            "target": {
                                "selector": "#test-consent-banner",
                                "displayFilter": True,
                            },
                        },
                    }
                ],
                "methods": [
                    {
                        "name": "OPEN_OPTIONS",
                        "action": {
                            "type": "click",
                            "target": {"selector": "#test-options-btn"},
                        },
                    },
                    {
                        "name": "DO_CONSENT",
                        "action": {
                            "type": "consent",
                            "consents": [
                                {
                                    "trueAction": {
                                        "type": "click",
                                        "target": {"selector": "#accept-analytics"},
                                    }
                                }
                            ],
                        },
                    },
                    {
                        "name": "SAVE_CONSENT",
                        "action": {
                            "type": "click",
                            "target": {"selector": "#save-consent-btn"},
                        },
                    },
                ],
            }
        }

    def test_cmp_rule_from_dict(self, sample_rules: dict[str, Any]) -> None:
        """Test parsing a CMP rule from dictionary."""
        from webctl.daemon.detectors.consent_o_matic.rules import CMPRule

        rule = CMPRule.from_dict("TestCMP", sample_rules["TestCMP"])

        assert rule.name == "TestCMP"
        assert len(rule.detect_cmp) > 0
        assert len(rule.detect_popup) > 0
        assert len(rule.opt_in) > 0

    @pytest.mark.asyncio
    async def test_rules_manager_cache(
        self, tmp_path: Path, sample_rules: dict[str, Any]
    ) -> None:
        """Test that rules are cached correctly."""
        from webctl.daemon.detectors.consent_o_matic.rules import RulesManager

        # Mock the cache paths
        cache_dir = tmp_path / "consent-o-matic"
        cache_dir.mkdir(parents=True)

        with (
            patch(
                "webctl.daemon.detectors.consent_o_matic.rules._get_cache_path",
                return_value=cache_dir / "rules.json",
            ),
            patch(
                "webctl.daemon.detectors.consent_o_matic.rules._get_cache_meta_path",
                return_value=cache_dir / "cache_meta.json",
            ),
        ):
            manager = RulesManager()

            # Mock fetch to return sample rules
            async def mock_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
                return sample_rules

            manager._fetch_rules = mock_fetch  # type: ignore

            # First call should fetch and cache
            rules = await manager.get_rules()
            assert "TestCMP" in rules

            # Verify cache files were created
            assert (cache_dir / "rules.json").exists()
            assert (cache_dir / "cache_meta.json").exists()


class TestActionExecutor:
    """Tests for the ActionExecutor class."""

    @pytest.fixture
    def mock_page(self) -> MagicMock:
        """Create a mock Playwright page."""
        page = MagicMock()
        page.locator = MagicMock(return_value=MagicMock())
        return page

    @pytest.fixture
    def executor(self) -> Any:
        """Create an ActionExecutor instance."""
        from webctl.daemon.detectors.consent_o_matic.actions import ActionExecutor

        return ActionExecutor(accept_all=True)

    @pytest.mark.asyncio
    async def test_execute_wait(self, executor: Any, mock_page: MagicMock) -> None:
        """Test wait action execution."""
        action = {"type": "wait", "waitTime": 100}

        # Should complete without error
        result = await executor.execute(action, mock_page)
        assert result is True

    @pytest.mark.asyncio
    async def test_execute_list(self, executor: Any, mock_page: MagicMock) -> None:
        """Test list action execution."""
        action = {
            "type": "list",
            "actions": [
                {"type": "wait", "waitTime": 10},
                {"type": "wait", "waitTime": 10},
            ],
        }

        result = await executor.execute(action, mock_page)
        assert result is True

    @pytest.mark.asyncio
    async def test_execute_noop_for_unsupported(
        self, executor: Any, mock_page: MagicMock
    ) -> None:
        """Test that unsupported actions return False."""
        action = {"type": "unknown_action_type"}

        result = await executor.execute(action, mock_page)
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_click_missing_target(
        self, executor: Any, mock_page: MagicMock
    ) -> None:
        """Test click action with missing target."""
        action = {"type": "click"}

        result = await executor.execute(action, mock_page)
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_consent_accept_all(
        self, executor: Any, mock_page: MagicMock
    ) -> None:
        """Test consent action in accept_all mode."""
        # Mock the target resolver to return a locator
        mock_locator = AsyncMock()
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.click = AsyncMock()

        with patch.object(
            executor._target_resolver, "resolve", return_value=mock_locator
        ):
            action = {
                "type": "consent",
                "consents": [
                    {
                        "trueAction": {
                            "type": "click",
                            "target": {"selector": "#accept"},
                        }
                    }
                ],
            }

            result = await executor.execute(action, mock_page)
            assert result is True


class TestTargetResolver:
    """Tests for the TargetResolver class."""

    @pytest.fixture
    def resolver(self) -> Any:
        """Create a TargetResolver instance."""
        from webctl.daemon.detectors.consent_o_matic.actions import TargetResolver

        return TargetResolver()

    @pytest.mark.asyncio
    async def test_resolve_string_target(self, resolver: Any) -> None:
        """Test resolving a simple string selector."""
        mock_page = MagicMock()
        mock_locator = AsyncMock()
        mock_locator.count = AsyncMock(return_value=1)
        mock_locator.first = mock_locator
        mock_page.locator = MagicMock(return_value=mock_locator)

        result = await resolver.resolve("#test-selector", mock_page)

        mock_page.locator.assert_called_once_with("#test-selector")
        assert result is not None

    @pytest.mark.asyncio
    async def test_resolve_dict_target(self, resolver: Any) -> None:
        """Test resolving a dict target with selector."""
        mock_page = MagicMock()
        mock_locator = AsyncMock()
        mock_locator.count = AsyncMock(return_value=1)
        mock_locator.first = mock_locator
        mock_page.locator = MagicMock(return_value=mock_locator)

        result = await resolver.resolve({"selector": "#test-selector"}, mock_page)

        mock_page.locator.assert_called_with("#test-selector")
        assert result is not None

    @pytest.mark.asyncio
    async def test_resolve_no_matches(self, resolver: Any) -> None:
        """Test resolving when no elements match."""
        mock_page = MagicMock()
        mock_locator = AsyncMock()
        mock_locator.count = AsyncMock(return_value=0)
        mock_page.locator = MagicMock(return_value=mock_locator)

        result = await resolver.resolve("#nonexistent", mock_page)

        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_with_text_filter(self, resolver: Any) -> None:
        """Test resolving with text filter."""
        mock_page = MagicMock()
        mock_locator = AsyncMock()
        mock_locator.count = AsyncMock(return_value=1)
        mock_locator.first = mock_locator
        mock_locator.filter = MagicMock(return_value=mock_locator)
        mock_page.locator = MagicMock(return_value=mock_locator)

        result = await resolver.resolve(
            {"selector": "button", "textFilter": "Accept"}, mock_page
        )

        mock_locator.filter.assert_called_once_with(has_text="Accept")
        assert result is not None


class TestConsentOMaticEngine:
    """Tests for the ConsentOMaticEngine class."""

    @pytest.fixture
    def sample_rules(self) -> dict[str, Any]:
        """Sample rules for testing."""
        return {
            "TestCMP": {
                "detectors": [
                    {
                        "presentMatcher": {
                            "type": "css",
                            "target": {"selector": "#consent-banner"},
                        },
                        "showingMatcher": {
                            "type": "css",
                            "target": {"selector": "#consent-banner"},
                        },
                    }
                ],
                "methods": [
                    {
                        "name": "SAVE_CONSENT",
                        "action": {
                            "type": "click",
                            "target": {"selector": "#accept-all"},
                        },
                    }
                ],
            }
        }

    @pytest.mark.asyncio
    async def test_detect_cmp_present(self, sample_rules: dict[str, Any]) -> None:
        """Test CMP detection when banner is present."""
        from webctl.daemon.detectors.consent_o_matic.engine import ConsentOMaticEngine

        engine = ConsentOMaticEngine()

        # Mock the rules manager
        engine._rules_manager.get_raw_rules = AsyncMock(return_value=sample_rules)

        # Mock the target resolver
        mock_locator = AsyncMock()
        mock_locator.is_visible = AsyncMock(return_value=True)
        engine._target_resolver.resolve = AsyncMock(return_value=mock_locator)

        # Mock page
        mock_page = MagicMock()

        cmp_name = await engine._detect_cmp(mock_page, sample_rules)
        assert cmp_name == "TestCMP"

    @pytest.mark.asyncio
    async def test_detect_cmp_not_present(self, sample_rules: dict[str, Any]) -> None:
        """Test CMP detection when no banner is present."""
        from webctl.daemon.detectors.consent_o_matic.engine import ConsentOMaticEngine

        engine = ConsentOMaticEngine()

        # Mock the target resolver to return None (no match)
        engine._target_resolver.resolve = AsyncMock(return_value=None)

        # Mock page
        mock_page = MagicMock()

        cmp_name = await engine._detect_cmp(mock_page, sample_rules)
        assert cmp_name is None

    @pytest.mark.asyncio
    async def test_detect_and_handle_success(
        self, sample_rules: dict[str, Any]
    ) -> None:
        """Test successful consent handling."""
        from webctl.daemon.detectors.consent_o_matic.engine import ConsentOMaticEngine

        engine = ConsentOMaticEngine()

        # Mock rules manager
        engine._rules_manager.get_raw_rules = AsyncMock(return_value=sample_rules)

        # Mock successful detection and handling
        mock_locator = AsyncMock()
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.click = AsyncMock()

        # First resolve returns a match (CMP detected)
        # After handling, resolve returns None (popup gone)
        resolve_call_count = 0

        async def mock_resolve(*args: Any, **kwargs: Any) -> Any:
            nonlocal resolve_call_count
            resolve_call_count += 1
            if resolve_call_count <= 2:
                return mock_locator
            return None

        engine._target_resolver.resolve = mock_resolve  # type: ignore

        # Mock action executor
        engine._action_executor.execute = AsyncMock(return_value=True)

        # Mock page
        mock_page = MagicMock()

        result = await engine.detect_and_handle(mock_page)

        assert result.handled is True
        assert result.cmp_name == "TestCMP"
        assert len(result.methods_executed) > 0

    @pytest.mark.asyncio
    async def test_detect_and_handle_no_rules(self) -> None:
        """Test handling when no rules are loaded."""
        from webctl.daemon.detectors.consent_o_matic.engine import ConsentOMaticEngine

        engine = ConsentOMaticEngine()

        # Mock empty rules
        engine._rules_manager.get_raw_rules = AsyncMock(return_value={})

        mock_page = MagicMock()

        result = await engine.detect_and_handle(mock_page)

        assert result.handled is False
        assert result.error == "No rules loaded"


class TestCookieBannerIntegration:
    """Test integration with CookieBannerDismisser."""

    @pytest.mark.asyncio
    async def test_consent_o_matic_used_first(self) -> None:
        """Test that Consent-O-Matic is tried before fallback strategies."""
        from webctl.daemon.detectors.consent_o_matic import ConsentOMaticResult
        from webctl.daemon.detectors.cookie_banner import CookieBannerDismisser

        dismisser = CookieBannerDismisser()

        # Mock successful Consent-O-Matic handling
        mock_result = ConsentOMaticResult(
            handled=True, cmp_name="TestCMP", methods_executed=["SAVE_CONSENT"]
        )
        dismisser._consent_o_matic.detect_and_handle = AsyncMock(
            return_value=mock_result
        )

        # Mock page
        mock_page = MagicMock()

        result = await dismisser.detect_and_dismiss(mock_page)

        assert result.detected is True
        assert result.dismissed is True
        assert result.method == "consent_o_matic"
        assert result.details["cmp"] == "TestCMP"

    @pytest.mark.asyncio
    async def test_fallback_used_when_consent_o_matic_fails(self) -> None:
        """Test that fallback strategies are used when Consent-O-Matic fails."""
        from webctl.daemon.detectors.consent_o_matic import ConsentOMaticResult
        from webctl.daemon.detectors.cookie_banner import CookieBannerDismisser

        dismisser = CookieBannerDismisser()

        # Mock failed Consent-O-Matic handling
        mock_result = ConsentOMaticResult(handled=False, cmp_name=None)
        dismisser._consent_o_matic.detect_and_handle = AsyncMock(
            return_value=mock_result
        )

        # Mock page with no consent elements
        mock_page = MagicMock()
        mock_page.locator = MagicMock(
            return_value=AsyncMock(count=AsyncMock(return_value=0))
        )
        mock_page.get_by_role = MagicMock(
            return_value=AsyncMock(count=AsyncMock(return_value=0))
        )
        mock_page.content = AsyncMock(return_value="<html></html>")

        # Should return not detected since no fallback matches either
        result = await dismisser.detect_and_dismiss(mock_page)

        assert result.dismissed is False
