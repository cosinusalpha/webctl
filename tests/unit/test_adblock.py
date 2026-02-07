"""Unit tests for adblock filtering integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFilterParser:
    """Tests for the filter parser."""

    def test_parse_network_block_filter(self) -> None:
        """Test parsing a basic blocking filter."""
        from webctl.daemon.adblock.parser import parse_network_filter

        result = parse_network_filter("||example.com^")
        assert result is not None
        assert result.is_hostname_anchor is True
        assert result.hostname == "example.com"
        assert result.is_exception is False

    def test_parse_network_exception_filter(self) -> None:
        """Test parsing an exception (whitelist) filter."""
        from webctl.daemon.adblock.parser import parse_network_filter

        result = parse_network_filter("@@||example.com^")
        assert result is not None
        assert result.is_exception is True
        assert result.hostname == "example.com"

    def test_parse_network_filter_with_modifiers(self) -> None:
        """Test parsing a filter with modifiers."""
        from webctl.daemon.adblock.parser import RequestType, parse_network_filter

        result = parse_network_filter("||ads.com^$third-party,script")
        assert result is not None
        assert result.hostname == "ads.com"
        assert result.third_party is True
        assert RequestType.SCRIPT in result.request_types

    def test_parse_network_filter_with_domain(self) -> None:
        """Test parsing a filter with domain constraint."""
        from webctl.daemon.adblock.parser import parse_network_filter

        result = parse_network_filter("||tracker.com^$domain=example.com|other.com")
        assert result is not None
        assert "example.com" in result.domains
        assert "other.com" in result.domains

    def test_parse_network_filter_with_redirect(self) -> None:
        """Test parsing a filter with redirect option."""
        from webctl.daemon.adblock.parser import parse_network_filter

        result = parse_network_filter("||google-analytics.com/analytics.js$redirect=noopjs")
        assert result is not None
        assert result.redirect == "noopjs"

    def test_parse_cosmetic_filter(self) -> None:
        """Test parsing a cosmetic (element hiding) filter."""
        from webctl.daemon.adblock.parser import parse_cosmetic_filter

        result = parse_cosmetic_filter("##.ad-banner")
        assert result is not None
        assert result.selector == ".ad-banner"
        assert result.is_exception is False
        assert len(result.domains) == 0  # Global filter

    def test_parse_cosmetic_filter_with_domain(self) -> None:
        """Test parsing a domain-specific cosmetic filter."""
        from webctl.daemon.adblock.parser import parse_cosmetic_filter

        result = parse_cosmetic_filter("example.com##.ad-banner")
        assert result is not None
        assert result.selector == ".ad-banner"
        assert "example.com" in result.domains

    def test_parse_cosmetic_exception(self) -> None:
        """Test parsing a cosmetic exception filter."""
        from webctl.daemon.adblock.parser import parse_cosmetic_filter

        result = parse_cosmetic_filter("example.com#@#.ad-banner")
        assert result is not None
        assert result.is_exception is True

    def test_parse_scriptlet_filter(self) -> None:
        """Test parsing a scriptlet filter."""
        from webctl.daemon.adblock.parser import parse_scriptlet_filter

        result = parse_scriptlet_filter("example.com##+js(set-constant, adblock, false)")
        assert result is not None
        assert result.scriptlet_name == "set-constant"
        assert result.args == ["adblock", "false"]
        assert "example.com" in result.domains

    def test_parse_filter_list(self) -> None:
        """Test parsing a complete filter list."""
        from webctl.daemon.adblock.parser import parse_filter_list

        content = """
! This is a comment
[Adblock Plus 2.0]
||ads.example.com^
@@||allowed.example.com^
##.advertisement
example.com##.sponsored
example.com##+js(abort-on-property-read, adblock)
"""
        result = parse_filter_list(content)
        assert len(result.network_filters) == 2
        assert len(result.cosmetic_filters) == 2
        assert len(result.scriptlet_filters) == 1


class TestNetworkMatcher:
    """Tests for the network filter matcher."""

    def test_block_matching_url(self) -> None:
        """Test that matching URLs are blocked."""
        from webctl.daemon.adblock.matcher import NetworkFilterMatcher
        from webctl.daemon.adblock.parser import parse_network_filter

        matcher = NetworkFilterMatcher()
        filter_obj = parse_network_filter("||ads.example.com^")
        assert filter_obj is not None
        matcher.add_filters([filter_obj])

        result = matcher.should_block("https://ads.example.com/banner.js")
        assert result.blocked is True

    def test_allow_non_matching_url(self) -> None:
        """Test that non-matching URLs are allowed."""
        from webctl.daemon.adblock.matcher import NetworkFilterMatcher
        from webctl.daemon.adblock.parser import parse_network_filter

        matcher = NetworkFilterMatcher()
        filter_obj = parse_network_filter("||ads.example.com^")
        assert filter_obj is not None
        matcher.add_filters([filter_obj])

        result = matcher.should_block("https://www.example.com/page.html")
        assert result.blocked is False

    def test_exception_overrides_block(self) -> None:
        """Test that exception filters override block filters."""
        from webctl.daemon.adblock.matcher import NetworkFilterMatcher
        from webctl.daemon.adblock.parser import parse_network_filter

        matcher = NetworkFilterMatcher()
        block = parse_network_filter("||example.com^")
        exception = parse_network_filter("@@||example.com/allowed^")
        assert block is not None
        assert exception is not None
        matcher.add_filters([block, exception])

        # Regular page should be blocked
        result = matcher.should_block("https://example.com/ads")
        assert result.blocked is True

        # Exception path should be allowed
        result = matcher.should_block("https://example.com/allowed")
        assert result.blocked is False

    def test_subdomain_matching(self) -> None:
        """Test that subdomain matching works correctly."""
        from webctl.daemon.adblock.matcher import NetworkFilterMatcher
        from webctl.daemon.adblock.parser import parse_network_filter

        matcher = NetworkFilterMatcher()
        filter_obj = parse_network_filter("||ads.com^")
        assert filter_obj is not None
        matcher.add_filters([filter_obj])

        # Direct match
        result = matcher.should_block("https://ads.com/")
        assert result.blocked is True

        # Subdomain match
        result = matcher.should_block("https://www.ads.com/")
        assert result.blocked is True

        # Different domain
        result = matcher.should_block("https://notads.com/")
        assert result.blocked is False

    def test_third_party_filtering(self) -> None:
        """Test third-party request filtering."""
        from webctl.daemon.adblock.matcher import NetworkFilterMatcher
        from webctl.daemon.adblock.parser import parse_network_filter

        matcher = NetworkFilterMatcher()
        filter_obj = parse_network_filter("||tracker.com^$third-party")
        assert filter_obj is not None
        matcher.add_filters([filter_obj])

        # Third-party request should be blocked
        result = matcher.should_block(
            "https://tracker.com/track.js", source_hostname="example.com"
        )
        assert result.blocked is True

        # First-party request should be allowed
        result = matcher.should_block(
            "https://tracker.com/track.js", source_hostname="tracker.com"
        )
        assert result.blocked is False

    def test_redirect_resource(self) -> None:
        """Test that redirect resources are returned."""
        from webctl.daemon.adblock.matcher import NetworkFilterMatcher
        from webctl.daemon.adblock.parser import parse_network_filter

        matcher = NetworkFilterMatcher()
        filter_obj = parse_network_filter("||analytics.com/script.js$redirect=noopjs")
        assert filter_obj is not None
        matcher.add_filters([filter_obj])

        result = matcher.should_block("https://analytics.com/script.js")
        assert result.blocked is True
        assert result.redirect == "noopjs"


class TestCosmeticHandler:
    """Tests for the cosmetic filter handler."""

    def test_get_selectors_for_domain(self) -> None:
        """Test getting selectors for a specific domain."""
        from webctl.daemon.adblock.cosmetic import CosmeticFilterHandler
        from webctl.daemon.adblock.parser import parse_cosmetic_filter

        handler = CosmeticFilterHandler()
        global_filter = parse_cosmetic_filter("##.ad-banner")
        domain_filter = parse_cosmetic_filter("example.com##.sponsored")
        assert global_filter is not None
        assert domain_filter is not None
        handler.add_filters([global_filter, domain_filter])

        # example.com should have both selectors
        selectors = handler.get_selectors_for_domain("example.com")
        assert ".ad-banner" in selectors
        assert ".sponsored" in selectors

        # other.com should only have global selector
        selectors = handler.get_selectors_for_domain("other.com")
        assert ".ad-banner" in selectors
        assert ".sponsored" not in selectors

    def test_exception_removes_selector(self) -> None:
        """Test that exceptions remove selectors."""
        from webctl.daemon.adblock.cosmetic import CosmeticFilterHandler
        from webctl.daemon.adblock.parser import parse_cosmetic_filter

        handler = CosmeticFilterHandler()
        global_filter = parse_cosmetic_filter("##.ad-banner")
        exception = parse_cosmetic_filter("example.com#@#.ad-banner")
        assert global_filter is not None
        assert exception is not None
        handler.add_filters([global_filter, exception])

        # example.com should NOT have the selector due to exception
        selectors = handler.get_selectors_for_domain("example.com")
        assert ".ad-banner" not in selectors

        # other.com should still have it
        selectors = handler.get_selectors_for_domain("other.com")
        assert ".ad-banner" in selectors

    def test_get_css_for_domain(self) -> None:
        """Test CSS generation for a domain."""
        from webctl.daemon.adblock.cosmetic import CosmeticFilterHandler
        from webctl.daemon.adblock.parser import parse_cosmetic_filter

        handler = CosmeticFilterHandler()
        filter1 = parse_cosmetic_filter("##.ad-banner")
        filter2 = parse_cosmetic_filter("##.sponsored")
        assert filter1 is not None
        assert filter2 is not None
        handler.add_filters([filter1, filter2])

        css = handler.get_css_for_domain("example.com")
        assert "display: none !important" in css
        assert ".ad-banner" in css
        assert ".sponsored" in css


class TestScriptletHandler:
    """Tests for the scriptlet handler."""

    def test_get_scripts_for_domain(self) -> None:
        """Test getting scripts for a specific domain."""
        from webctl.daemon.adblock.parser import parse_scriptlet_filter
        from webctl.daemon.adblock.scriptlets import ScriptletHandler

        handler = ScriptletHandler()
        filter_obj = parse_scriptlet_filter("example.com##+js(set-constant, adblock, false)")
        assert filter_obj is not None
        handler.add_filters([filter_obj])

        # example.com should have the scriptlet
        scripts = handler.get_scripts_for_domain("example.com")
        assert len(scripts) == 1
        assert "adblock" in scripts[0]
        assert "false" in scripts[0]

        # other.com should not have the scriptlet
        scripts = handler.get_scripts_for_domain("other.com")
        assert len(scripts) == 0

    def test_scriptlet_alias(self) -> None:
        """Test that scriptlet aliases work."""
        from webctl.daemon.adblock.parser import parse_scriptlet_filter
        from webctl.daemon.adblock.scriptlets import ScriptletHandler

        handler = ScriptletHandler()
        # 'aopr' is an alias for 'abort-on-property-read'
        filter_obj = parse_scriptlet_filter("example.com##+js(aopr, detectAdblock)")
        assert filter_obj is not None
        handler.add_filters([filter_obj])

        scripts = handler.get_scripts_for_domain("example.com")
        assert len(scripts) == 1
        assert "detectAdblock" in scripts[0]


class TestResources:
    """Tests for redirect resources."""

    def test_get_redirect_resource(self) -> None:
        """Test getting redirect resources."""
        from webctl.daemon.adblock.resources import get_redirect_resource

        # 1x1 GIF
        resource = get_redirect_resource("1x1.gif")
        assert resource is not None
        content, content_type = resource
        assert content_type == "image/gif"
        assert content.startswith(b"GIF89a")

        # noopjs
        resource = get_redirect_resource("noopjs")
        assert resource is not None
        content, content_type = resource
        assert content_type == "application/javascript"

    def test_get_redirect_resource_alias(self) -> None:
        """Test that resource aliases work."""
        from webctl.daemon.adblock.resources import get_redirect_resource

        # 'noop.js' is an alias for 'noopjs'
        resource = get_redirect_resource("noop.js")
        assert resource is not None
        _, content_type = resource
        assert content_type == "application/javascript"

    def test_get_redirect_resource_unknown(self) -> None:
        """Test that unknown resources return None."""
        from webctl.daemon.adblock.resources import get_redirect_resource

        resource = get_redirect_resource("unknown-resource")
        assert resource is None


class TestFilterListManager:
    """Tests for the filter list manager."""

    @pytest.mark.asyncio
    async def test_cache_metadata(self, tmp_path: Path) -> None:
        """Test that cache metadata is saved correctly."""
        from webctl.daemon.adblock.filter_lists import FilterListManager

        cache_dir = tmp_path / "adblock"
        lists_dir = cache_dir / "lists"
        lists_dir.mkdir(parents=True)

        with (
            patch(
                "webctl.daemon.adblock.filter_lists._get_cache_dir",
                return_value=cache_dir,
            ),
            patch(
                "webctl.daemon.adblock.filter_lists._get_list_cache_path",
                side_effect=lambda name: lists_dir / f"{name}.txt",
            ),
            patch(
                "webctl.daemon.adblock.filter_lists._get_cache_meta_path",
                return_value=cache_dir / "cache_meta.json",
            ),
        ):
            manager = FilterListManager()

            # Mock fetch to return sample content
            async def mock_fetch(name: str) -> str:
                return "||example.com^\n##.ad-banner"

            manager._fetch_list = mock_fetch  # type: ignore

            # Fetch lists (should cache them)
            lists = await manager.get_filter_lists(["easylist"])
            assert "easylist" in lists

            # Verify cache files were created
            assert (cache_dir / "cache_meta.json").exists()
            assert (lists_dir / "easylist.txt").exists()


class TestAdblockEngine:
    """Tests for the adblock engine."""

    @pytest.fixture
    def mock_page(self) -> MagicMock:
        """Create a mock Playwright page."""
        page = MagicMock()
        page.route = AsyncMock()
        page.on = MagicMock()
        page.add_style_tag = AsyncMock()
        page.add_init_script = AsyncMock()
        return page

    @pytest.mark.asyncio
    async def test_engine_initialization(self) -> None:
        """Test that the engine initializes correctly."""
        from webctl.daemon.adblock.engine import AdblockEngine

        # Mock filter list manager to avoid network requests
        with patch(
            "webctl.daemon.adblock.engine.get_filter_list_manager"
        ) as mock_manager:
            mock_manager.return_value.get_filter_lists = AsyncMock(
                return_value={
                    "test": "||ads.com^\n##.ad-banner"
                }
            )

            engine = AdblockEngine()
            await engine.initialize()

            assert engine._initialized is True

    @pytest.mark.asyncio
    async def test_engine_setup_page(self, mock_page: MagicMock) -> None:
        """Test that page setup installs handlers correctly."""
        from webctl.daemon.adblock.engine import AdblockEngine

        with patch(
            "webctl.daemon.adblock.engine.get_filter_list_manager"
        ) as mock_manager:
            mock_manager.return_value.get_filter_lists = AsyncMock(
                return_value={"test": "||ads.com^"}
            )

            engine = AdblockEngine()
            await engine.setup_page(mock_page)

            # Verify route handler was installed
            mock_page.route.assert_called_once()
            # Verify navigation handler was installed
            mock_page.on.assert_called()

    @pytest.mark.asyncio
    async def test_engine_get_stats(self) -> None:
        """Test that stats are tracked correctly."""
        from webctl.daemon.adblock.engine import AdblockEngine

        with patch(
            "webctl.daemon.adblock.engine.get_filter_list_manager"
        ) as mock_manager:
            mock_manager.return_value.get_filter_lists = AsyncMock(return_value={})

            engine = AdblockEngine()
            await engine.initialize()

            stats = engine.get_stats()
            assert "requests_checked" in stats
            assert "requests_blocked" in stats
            assert "requests_redirected" in stats


class TestIntegrationWithConfig:
    """Tests for integration with config."""

    def test_config_adblock_settings(self, tmp_path: Path) -> None:
        """Test that adblock config settings are loaded correctly."""
        import json

        from webctl.config import WebctlConfig

        config_path = tmp_path / "config.json"
        with open(config_path, "w") as f:
            json.dump(
                {
                    "adblock_enabled": False,
                    "adblock_lists": ["easylist"],
                },
                f,
            )

        config = WebctlConfig.load(config_path)
        assert config.adblock_enabled is False
        assert config.adblock_lists == ["easylist"]

    def test_config_adblock_defaults(self, tmp_path: Path) -> None:
        """Test that adblock defaults are correct."""
        import json

        from webctl.config import WebctlConfig

        config_path = tmp_path / "config.json"
        with open(config_path, "w") as f:
            json.dump({}, f)

        config = WebctlConfig.load(config_path)
        assert config.adblock_enabled is True  # Enabled by default
        assert config.adblock_lists is None  # Use default lists
