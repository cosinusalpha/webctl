"""
RFC SS8.3: Structure View (dom-lite) - MAY

Highly filtered structural fallback containing:
- forms
- inputs
- tables
- images
- links

Never default. Used when a11y tree is insufficient.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page

from .redaction import is_sensitive_field


@dataclass
class DomLiteOptions:
    """Options for DOM-lite view extraction."""

    include_forms: bool = True
    include_tables: bool = True
    include_images: bool = True
    include_links: bool = True
    max_table_rows: int = 50
    max_links: int = 100


async def extract_dom_lite_view(
    page: Page, options: DomLiteOptions | None = None
) -> AsyncIterator[dict[str, Any]]:
    """
    Extract structural DOM elements as fallback view.

    Returns filtered set of:
    - forms with inputs
    - tables with headers and rows
    - images with alt text
    - links with href and text
    """
    options = options or DomLiteOptions()

    # Extract all relevant elements in one evaluate call
    data = await page.evaluate(
        """
        (opts) => {
            const result = {
                forms: [],
                tables: [],
                images: [],
                links: []
            };

            // Forms
            if (opts.include_forms) {
                document.querySelectorAll('form').forEach((form, idx) => {
                    const formData = {
                        tag: 'form',
                        id: form.id || null,
                        name: form.name || null,
                        action: form.action || null,
                        method: form.method || 'GET',
                        inputs: []
                    };

                    form.querySelectorAll('input, select, textarea, button').forEach(input => {
                        formData.inputs.push({
                            tag: input.tagName.toLowerCase(),
                            type: input.type || null,
                            name: input.name || null,
                            id: input.id || null,
                            placeholder: input.placeholder || null,
                            required: input.required || false,
                            value: input.type === 'password' ? null : (input.value || null)
                        });
                    });

                    result.forms.push(formData);
                });
            }

            // Tables
            if (opts.include_tables) {
                document.querySelectorAll('table').forEach((table, idx) => {
                    const tableData = {
                        tag: 'table',
                        id: table.id || null,
                        headers: [],
                        rows: []
                    };

                    // Headers
                    table.querySelectorAll('th').forEach(th => {
                        tableData.headers.push(th.textContent.trim());
                    });

                    // Rows (limited)
                    const rows = table.querySelectorAll('tr');
                    const maxRows = Math.min(rows.length, opts.max_table_rows);
                    for (let i = 0; i < maxRows; i++) {
                        const cells = [];
                        rows[i].querySelectorAll('td').forEach(td => {
                            cells.push(td.textContent.trim().slice(0, 200));
                        });
                        if (cells.length > 0) {
                            tableData.rows.push(cells);
                        }
                    }

                    if (tableData.headers.length > 0 || tableData.rows.length > 0) {
                        result.tables.push(tableData);
                    }
                });
            }

            // Images
            if (opts.include_images) {
                document.querySelectorAll('img').forEach(img => {
                    if (img.src && img.width > 50 && img.height > 50) {
                        result.images.push({
                            tag: 'img',
                            src: img.src,
                            alt: img.alt || null,
                            width: img.width,
                            height: img.height
                        });
                    }
                });
            }

            // Links (limited)
            if (opts.include_links) {
                const links = document.querySelectorAll('a[href]');
                const maxLinks = Math.min(links.length, opts.max_links);
                for (let i = 0; i < maxLinks; i++) {
                    const link = links[i];
                    result.links.push({
                        tag: 'a',
                        href: link.href,
                        text: link.textContent.trim().slice(0, 100)
                    });
                }
            }

            return result;
        }
    """,
        {
            "include_forms": options.include_forms,
            "include_tables": options.include_tables,
            "include_images": options.include_images,
            "include_links": options.include_links,
            "max_table_rows": options.max_table_rows,
            "max_links": options.max_links,
        },
    )

    # Redact sensitive form values
    for form in data.get("forms", []):
        for inp in form.get("inputs", []):
            if inp.get("type") == "password" or inp.get("name") and is_sensitive_field(inp["name"]):
                inp["value"] = "<redacted>"

    yield {
        "type": "item",
        "view": "dom-lite",
        "url": page.url,
        "title": await page.title(),
        "forms": data.get("forms", []),
        "tables": data.get("tables", []),
        "images": data.get("images", []),
        "links": data.get("links", []),
    }
