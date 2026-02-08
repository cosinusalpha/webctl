"""
Cosmetic filter handling for element hiding via CSS injection.

Injects CSS rules to hide ad elements on web pages.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .parser import CosmeticFilter

logger = logging.getLogger(__name__)


def _get_hostname_variants(hostname: str) -> list[str]:
    """Get all variants of a hostname for matching.

    For 'sub.example.com', returns ['sub.example.com', 'example.com', 'com'].
    """
    parts = hostname.split(".")
    variants = []
    for i in range(len(parts)):
        variants.append(".".join(parts[i:]))
    return variants


def _domain_matches(hostname: str, included: set[str], excluded: set[str]) -> bool:
    """Check if hostname matches domain constraints."""
    hostname = hostname.lower()
    hostname_variants = _get_hostname_variants(hostname)

    # Check exclusions first
    for variant in hostname_variants:
        if variant in excluded:
            return False

    # If no inclusions specified, matches
    if not included:
        return True

    # Check inclusions
    return any(variant in included for variant in hostname_variants)


class CosmeticFilterHandler:
    """Handle cosmetic (element hiding) filters."""

    def __init__(self) -> None:
        # Global filters (no domain constraint)
        self._global_filters: list[CosmeticFilter] = []
        self._global_exceptions: list[CosmeticFilter] = []

        # Domain-specific filters: domain -> list of filters
        self._domain_filters: dict[str, list[CosmeticFilter]] = {}
        self._domain_exceptions: dict[str, list[CosmeticFilter]] = {}

        # Statistics
        self._total_filters = 0

    def add_filters(self, filters: list[CosmeticFilter]) -> None:
        """Add cosmetic filters."""
        for f in filters:
            self._add_filter(f)

        logger.debug(
            "Added %d cosmetic filters (%d global, %d domain-specific)",
            self._total_filters,
            len(self._global_filters),
            sum(len(v) for v in self._domain_filters.values()),
        )

    def _add_filter(self, f: CosmeticFilter) -> None:
        """Add a single cosmetic filter."""
        self._total_filters += 1

        if not f.domains and not f.excluded_domains:
            # Global filter
            if f.is_exception:
                self._global_exceptions.append(f)
            else:
                self._global_filters.append(f)
        else:
            # Domain-specific filter
            index = self._domain_exceptions if f.is_exception else self._domain_filters

            # Index by each included domain
            if f.domains:
                for domain in f.domains:
                    if domain not in index:
                        index[domain] = []
                    index[domain].append(f)
            else:
                # Filter with only exclusions - treat as global with exclusions
                if f.is_exception:
                    self._global_exceptions.append(f)
                else:
                    self._global_filters.append(f)

    def get_selectors_for_domain(self, hostname: str) -> list[str]:
        """Get CSS selectors that should be hidden for a domain.

        Args:
            hostname: The hostname to get selectors for.

        Returns:
            List of CSS selectors to hide.
        """
        selectors: list[str] = []
        exception_selectors: set[str] = set()
        hostname = hostname.lower()

        # Collect exception selectors first
        for f in self._global_exceptions:
            if _domain_matches(hostname, f.domains, f.excluded_domains):
                exception_selectors.add(f.selector)

        # Domain-specific exceptions
        for variant in _get_hostname_variants(hostname):
            for f in self._domain_exceptions.get(variant, []):
                if _domain_matches(hostname, f.domains, f.excluded_domains):
                    exception_selectors.add(f.selector)

        # Collect global selectors (not excepted)
        for f in self._global_filters:
            if f.selector not in exception_selectors:
                if _domain_matches(hostname, f.domains, f.excluded_domains):
                    selectors.append(f.selector)

        # Collect domain-specific selectors
        for variant in _get_hostname_variants(hostname):
            for f in self._domain_filters.get(variant, []):
                if f.selector not in exception_selectors:
                    if _domain_matches(hostname, f.domains, f.excluded_domains):
                        selectors.append(f.selector)

        return selectors

    def get_css_for_domain(self, hostname: str) -> str:
        """Get CSS rules to hide ad elements for a domain.

        Args:
            hostname: The hostname to get CSS for.

        Returns:
            CSS string with hide rules.
        """
        selectors = self.get_selectors_for_domain(hostname)

        if not selectors:
            return ""

        # Batch selectors to avoid overly long CSS rules
        # CSS has limits on selector count per rule in some browsers
        css_rules: list[str] = []
        batch_size = 100

        for i in range(0, len(selectors), batch_size):
            batch = selectors[i : i + batch_size]
            # Escape any problematic characters in selectors
            safe_selectors = []
            for sel in batch:
                # Skip selectors that might cause issues
                if '"' in sel or "'" in sel and "\\" not in sel:
                    continue
                safe_selectors.append(sel)

            if safe_selectors:
                selector_str = ", ".join(safe_selectors)
                css_rules.append(f"{selector_str} {{ display: none !important; }}")

        return "\n".join(css_rules)

    async def apply_to_page(self, page: Page, hostname: str) -> None:
        """Apply cosmetic filters to a page by injecting CSS.

        Args:
            page: The Playwright page to apply filters to.
            hostname: The hostname of the page.
        """
        css = self.get_css_for_domain(hostname)

        if not css:
            return

        try:
            await page.add_style_tag(content=css)
            logger.debug("Injected cosmetic CSS for %s", hostname)
        except Exception as e:
            logger.debug("Failed to inject cosmetic CSS: %s", e)
