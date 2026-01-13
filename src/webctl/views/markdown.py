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

import re
from collections.abc import AsyncIterator
from typing import Any

from markdownify import markdownify
from playwright.async_api import Page

from .redaction import redact_secrets

MAX_CONTENT_LENGTH = 50000


async def extract_markdown_view(page: Page) -> AsyncIterator[dict[str, Any]]:
    """Extract readable content as markdown."""

    # Get main content, excluding navigation/chrome
    html = await page.evaluate(
        """
        () => {
            // Try semantic main content first
            const main = document.querySelector('main, article, [role="main"], .content, #content');
            if (main) return main.innerHTML;

            // Fallback: body minus nav/footer/aside/scripts
            const body = document.body.cloneNode(true);
            const remove = ['nav', 'footer', 'aside', 'script', 'style', 'noscript',
                           '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                           'header', '.nav', '.navigation', '.sidebar'];
            remove.forEach(sel => {
                body.querySelectorAll(sel).forEach(el => el.remove());
            });
            return body.innerHTML;
        }
    """
    )

    # Convert to markdown
    md = markdownify(
        html,
        heading_style="ATX",
        strip=["script", "style", "noscript"],
        escape_asterisks=False,
        escape_underscores=False,
    )

    # Clean up excessive whitespace
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r" +", " ", md)
    md = md.strip()

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
