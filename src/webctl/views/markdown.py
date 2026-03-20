"""
RFC SS8.2: Read View (md / text) - SHOULD

Rendered, visible content:
- headings
- paragraphs
- tables
- lists
- links

Bounded by size limits.
"""

import io
import re
from collections.abc import AsyncIterator
from importlib.resources import files
from typing import Any

from markitdown import MarkItDown
from markitdown._stream_info import StreamInfo
from playwright.async_api import Page

from .redaction import redact_secrets

MAX_CONTENT_LENGTH = 50000

_readability_js: str | None = None
_markitdown: MarkItDown | None = None


def _get_readability_js() -> str:
    """Load vendored Readability.js (cached)."""
    global _readability_js
    if _readability_js is None:
        js_path = files("webctl.views") / "vendor" / "Readability.js"
        _readability_js = js_path.read_text(encoding="utf-8")
    return _readability_js


def _get_markitdown() -> MarkItDown:
    """Get cached MarkItDown instance."""
    global _markitdown
    if _markitdown is None:
        _markitdown = MarkItDown()
    return _markitdown


async def _extract_readability(page: Page) -> str | None:
    """Try Readability.js extraction. Returns HTML or None."""
    js = _get_readability_js()
    result = await page.evaluate(
        """([js]) => {
            try {
                const script = new Function(js + '; return Readability;');
                const Readability = script();
                const doc = document.cloneNode(true);
                const article = new Readability(doc).parse();
                return article ? article.content : null;
            } catch(e) {
                return null;
            }
        }""",
        [js],
    )
    return result


def _html_to_markdown(html: str) -> str:
    """Convert HTML to markdown using MarkItDown."""
    mid = _get_markitdown()
    stream = io.BytesIO(html.encode("utf-8"))
    result = mid.convert(stream, stream_info=StreamInfo(extension=".html"))
    md = result.text_content or ""

    # Clean up excessive whitespace
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r" +", " ", md)
    md = md.strip()
    return md


async def extract_markdown_view(page: Page) -> AsyncIterator[dict[str, Any]]:
    """Extract readable content as markdown."""

    # Try Readability.js first (best for articles).
    # Fall back to full page content + MarkItDown (handles everything else).
    html = await _extract_readability(page)
    if not html:
        html = await page.content()

    md = _html_to_markdown(html)

    # Truncate if needed
    truncated = False
    if len(md) > MAX_CONTENT_LENGTH:
        md = md[:MAX_CONTENT_LENGTH]
        # Cut at last paragraph
        last_para = md.rfind("\n\n")
        if last_para > MAX_CONTENT_LENGTH // 2:
            md = md[:last_para]
        md += "\n\n[... content truncated ...]"
        truncated = True

    # Redact sensitive content
    md = redact_secrets(md)

    yield {
        "type": "item",
        "view": "md",
        "url": page.url,
        "title": await page.title(),
        "content": md,
        "truncated": truncated,
        "length": len(md),
    }
