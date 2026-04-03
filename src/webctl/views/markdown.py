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

MAX_CONTENT_LENGTH = 16000  # ~4000 tokens — keeps --read useful without flooding context
MAX_CONTENT_LINES = 200

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
    result: str | None = await page.evaluate(
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


_STRUCTURED_DATA_JS = """
(() => {
    const result = { jsonLd: [], og: {}, meta: {} };
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try { result.jsonLd.push(JSON.parse(s.textContent)); } catch(e) {}
    });
    document.querySelectorAll('meta[property^="og:"]').forEach(m => {
        const prop = m.getAttribute('property');
        if (prop && m.content) result.og[prop] = m.content;
    });
    ['description', 'keywords', 'author'].forEach(n => {
        const m = document.querySelector('meta[name="' + n + '"]');
        if (m && m.content) result.meta[n] = m.content;
    });
    return result;
})()
"""


def _extract_offers(item: dict[str, Any], lines: list[str]) -> None:
    """Extract price/availability from offers field into lines."""
    offers = item.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = offers.get("price") or offers.get("lowPrice")
    currency = offers.get("priceCurrency", "")
    if price:
        lines.append(f"- **Price: {price} {currency}**")
    avail = offers.get("availability", "")
    if avail:
        avail = avail.rsplit("/", 1)[-1]  # schema.org/InStock → InStock
        lines.append(f"- Availability: {avail}")


def _extract_rating(item: dict[str, Any], lines: list[str]) -> None:
    """Extract aggregateRating into lines."""
    rating = item.get("aggregateRating", {})
    if rating:
        val = rating.get("ratingValue", "")
        count = rating.get("reviewCount") or rating.get("ratingCount", "")
        if val:
            lines.append(f"- Rating: {val}/5 ({count} reviews)")


async def _extract_structured_data(page: Page) -> str:
    """Extract JSON-LD, Open Graph, and meta tags. Returns formatted markdown or empty string."""

    try:
        raw = await page.evaluate(_STRUCTURED_DATA_JS)
    except Exception:
        return ""

    if not raw:
        return ""

    lines: list[str] = []

    # Process JSON-LD — flatten nested @graph structures
    for entry in raw.get("jsonLd", []):
        items_to_process = [entry]
        if "@graph" in entry:
            items_to_process = entry["@graph"]

        for item in items_to_process:
            ld_type = item.get("@type", "")
            if isinstance(ld_type, list):
                ld_type = ld_type[0] if ld_type else ""

            # --- Products, Software, Books, Movies, Music ---
            if ld_type in ("Product", "SoftwareApplication", "Book", "Movie", "MusicAlbum"):
                lines.append(f"- **{ld_type}**: {item.get('name', 'N/A')}")
                if item.get("description"):
                    lines.append(f"- Description: {item['description'][:200]}")
                if item.get("brand"):
                    brand = item["brand"]
                    if isinstance(brand, dict):
                        brand = brand.get("name", str(brand))
                    lines.append(f"- Brand: {brand}")
                _extract_offers(item, lines)
                _extract_rating(item, lines)

            # --- Articles / News ---
            elif ld_type in ("NewsArticle", "Article", "BlogPosting", "ReportageNewsArticle"):
                lines.append(f"- **{ld_type}**: {item.get('headline', item.get('name', 'N/A'))}")
                if item.get("description"):
                    lines.append(f"- Description: {item['description'][:200]}")
                author = item.get("author", {})
                if isinstance(author, list):
                    author = author[0] if author else {}
                if isinstance(author, dict):
                    author = author.get("name", "")
                if author:
                    lines.append(f"- Author: {author}")
                if item.get("datePublished"):
                    lines.append(f"- Published: {item['datePublished']}")
                publisher = item.get("publisher", {})
                if isinstance(publisher, dict):
                    publisher = publisher.get("name", "")
                if publisher:
                    lines.append(f"- Publisher: {publisher}")

            # --- Local businesses / Restaurants ---
            elif ld_type in (
                "LocalBusiness",
                "Restaurant",
                "FoodEstablishment",
                "CafeOrCoffeeShop",
                "BarOrPub",
                "Store",
            ):
                lines.append(f"- **{ld_type}**: {item.get('name', 'N/A')}")
                addr = item.get("address", {})
                if isinstance(addr, dict):
                    parts = [
                        addr.get("streetAddress", ""),
                        addr.get("postalCode", ""),
                        addr.get("addressLocality", ""),
                    ]
                    addr_str = ", ".join(p for p in parts if p)
                    if addr_str:
                        lines.append(f"- Address: {addr_str}")
                if item.get("telephone"):
                    lines.append(f"- Phone: {item['telephone']}")
                if item.get("servesCuisine"):
                    cuisine = item["servesCuisine"]
                    if isinstance(cuisine, list):
                        cuisine = ", ".join(cuisine)
                    lines.append(f"- Cuisine: {cuisine}")
                if item.get("priceRange"):
                    lines.append(f"- Price range: {item['priceRange']}")
                _extract_rating(item, lines)

            # --- Events ---
            elif ld_type == "Event":
                lines.append(f"- **Event**: {item.get('name', 'N/A')}")
                if item.get("startDate"):
                    lines.append(f"- Date: {item['startDate']}")
                location = item.get("location", {})
                if isinstance(location, dict):
                    lines.append(f"- Location: {location.get('name', location.get('address', ''))}")
                elif isinstance(location, str):
                    lines.append(f"- Location: {location}")
                _extract_offers(item, lines)

            # --- FAQ ---
            elif ld_type == "FAQPage":
                questions = item.get("mainEntity", [])
                if questions:
                    lines.append(f"- **FAQ**: {len(questions)} questions")
                    for q in questions[:5]:
                        qname = q.get("name", "")
                        if qname:
                            lines.append(f"  - Q: {qname[:120]}")

            # --- Search results / Item lists ---
            elif ld_type == "ItemList":
                list_items = item.get("itemListElement", [])
                if list_items:
                    lines.append(f"- **ItemList**: {len(list_items)} items")
                    for li in list_items[:5]:
                        li_name = li.get("name", "")
                        li_url = li.get("url", "")
                        if li_name:
                            lines.append(f"  - {li_name}" + (f" ({li_url})" if li_url else ""))

            # --- Breadcrumbs ---
            elif ld_type == "BreadcrumbList":
                crumbs = item.get("itemListElement", [])
                if crumbs:
                    path = " > ".join(
                        c.get("item", {}).get("name", c.get("name", ""))
                        for c in sorted(crumbs, key=lambda c: c.get("position", 0))
                        if c.get("item", {}).get("name") or c.get("name")
                    )
                    if path:
                        lines.append(f"- Category: {path}")

            # --- WebPage / WebSite (generic fallback, only if nothing else matched) ---
            elif ld_type in ("WebPage", "WebSite") and not lines:
                if item.get("name"):
                    lines.append(f"- **{ld_type}**: {item['name']}")
                if item.get("description"):
                    lines.append(f"- Description: {item['description'][:200]}")

    # Open Graph fallback (if JSON-LD didn't give us key info)
    og = raw.get("og", {})
    if og and not any("Price:" in line for line in lines):
        if og.get("og:price:amount"):
            currency = og.get("og:price:currency", "")
            lines.append(f"- **Price: {og['og:price:amount']} {currency}**")
    if og.get("og:description") and not any("Description:" in line for line in lines):
        desc = og["og:description"][:200]
        lines.append(f"- Description: {desc}")

    # Meta description fallback
    meta = raw.get("meta", {})
    if meta.get("description") and not any("Description:" in line for line in lines):
        lines.append(f"- Description: {meta['description'][:200]}")

    if not lines:
        return ""

    return "## Page Info (structured data)\n" + "\n".join(lines) + "\n\n"


async def extract_markdown_view(page: Page) -> AsyncIterator[dict[str, Any]]:
    """Extract readable content as markdown."""

    structured = await _extract_structured_data(page)

    # Readability.js first (articles), full page + MarkItDown as fallback
    html = await _extract_readability(page)
    if not html:
        html = await page.content()

    md = _html_to_markdown(html)

    # Prepend structured data
    if structured:
        md = structured + md

    # Truncate if needed (by chars or lines, whichever hits first)
    truncated = False
    if len(md) > MAX_CONTENT_LENGTH:
        md = md[:MAX_CONTENT_LENGTH]
        last_para = md.rfind("\n\n")
        if last_para > MAX_CONTENT_LENGTH // 2:
            md = md[:last_para]
        md += "\n\n[... content truncated. Full page content exceeds display limit ...]"
        truncated = True
    md_lines = md.split("\n")
    if len(md_lines) > MAX_CONTENT_LINES:
        md = "\n".join(md_lines[:MAX_CONTENT_LINES])
        md += "\n\n[... content truncated at 200 lines. Full page content exceeds display limit ...]"
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
