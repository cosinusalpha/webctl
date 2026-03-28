"""
Tests for markdown view with Readability.js + MarkItDown extraction.
"""

import pytest

from webctl.views.markdown import (
    MAX_CONTENT_LENGTH,
    _get_readability_js,
    _html_to_markdown,
)


class TestReadabilityJsLoading:
    """Test that vendored Readability.js loads correctly."""

    def test_readability_js_loads(self):
        """Vendored Readability.js loads and is non-empty."""
        js = _get_readability_js()
        assert len(js) > 1000
        assert "Readability" in js

    def test_readability_js_cached(self):
        """Second call returns same object (cached)."""
        js1 = _get_readability_js()
        js2 = _get_readability_js()
        assert js1 is js2


class TestHtmlToMarkdown:
    """Test HTML to markdown conversion via MarkItDown."""

    def test_basic_conversion(self):
        md = _html_to_markdown("<h1>Hello</h1><p>World</p>")
        assert "Hello" in md
        assert "World" in md

    def test_preserves_text_content(self):
        md = _html_to_markdown("<p>Text</p><p>More</p>")
        assert "Text" in md
        assert "More" in md

    def test_whitespace_cleanup(self):
        md = _html_to_markdown("<p>A</p>\n\n\n\n\n<p>B</p>")
        assert "\n\n\n" not in md

    def test_links_preserved(self):
        md = _html_to_markdown('<a href="https://example.com">Link</a>')
        assert "Link" in md
        assert "example.com" in md

    def test_strips_scripts(self):
        """MarkItDown strips script tags automatically."""
        md = _html_to_markdown("<p>Text</p><script>alert(1)</script>")
        assert "Text" in md
        assert "alert" not in md


class TestTruncation:
    """Test content truncation logic."""

    def test_short_content_not_truncated(self):
        md = _html_to_markdown("<p>Short</p>")
        assert "[... content truncated ...]" not in md

    def test_max_content_length_constant(self):
        assert MAX_CONTENT_LENGTH == 16000


class TestRedaction:
    """Test that redaction is applied."""

    @pytest.mark.asyncio
    async def test_redaction_applied(self):
        """Secrets in page content should be redacted."""
        from unittest.mock import AsyncMock, MagicMock

        page = MagicMock()
        page.url = "https://example.com"
        page.title = AsyncMock(return_value="Test")

        # Mock evaluate to return HTML with a fake AWS key
        async def mock_evaluate(js, *args):
            if "Readability" in js:
                return "<p>Key: AKIAIOSFODNN7EXAMPLE</p>"
            return ""

        page.evaluate = mock_evaluate

        from webctl.views.markdown import extract_markdown_view

        items = []
        async for item in extract_markdown_view(page):
            items.append(item)

        assert len(items) == 1
        assert "AKIAIOSFODNN7EXAMPLE" not in items[0]["content"]
