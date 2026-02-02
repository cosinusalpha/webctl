"""
URL matching engine for adblock network filters.

Uses multi-tier indexing for efficient matching:
1. Hostname hash map for ||domain.com^ rules (O(1))
2. Token index for partial URL patterns
3. Regex fallback for complex patterns
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .parser import NetworkFilter, RequestType

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Result of URL matching."""

    blocked: bool
    filter: NetworkFilter | None
    redirect: str | None = None


def _get_hostname_variants(hostname: str) -> list[str]:
    """Get all variants of a hostname for matching.

    For 'sub.example.com', returns ['sub.example.com', 'example.com', 'com'].
    """
    parts = hostname.split(".")
    variants = []
    for i in range(len(parts)):
        variants.append(".".join(parts[i:]))
    return variants


def _is_third_party(url_hostname: str, source_hostname: str | None) -> bool:
    """Check if a request is third-party."""
    if not source_hostname:
        return False

    # Get registrable domains (simplified: use last two parts)
    def get_domain(hostname: str) -> str:
        parts = hostname.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return hostname

    return get_domain(url_hostname) != get_domain(source_hostname)


def _domain_matches(
    hostname: str, included: set[str], excluded: set[str]
) -> bool:
    """Check if hostname matches domain constraints."""
    # If no domain constraints, matches all
    if not included and not excluded:
        return True

    hostname = hostname.lower()
    hostname_variants = _get_hostname_variants(hostname)

    # Check exclusions first
    for variant in hostname_variants:
        if variant in excluded:
            return False

    # If no inclusions specified, matches (only exclusions matter)
    if not included:
        return True

    # Check inclusions
    return any(variant in included for variant in hostname_variants)


class NetworkFilterMatcher:
    """Efficient URL matcher using multi-tier indexing."""

    def __init__(self) -> None:
        # Hostname index: hostname -> list of filters
        self._hostname_index: dict[str, list[NetworkFilter]] = {}

        # Exception hostname index
        self._exception_hostname_index: dict[str, list[NetworkFilter]] = {}

        # Generic filters (no hostname anchor)
        self._generic_filters: list[NetworkFilter] = []
        self._generic_exceptions: list[NetworkFilter] = []

        # Statistics
        self._total_filters = 0
        self._hostname_indexed = 0

    def add_filters(self, filters: list[NetworkFilter]) -> None:
        """Add filters to the matcher."""
        for f in filters:
            self._add_filter(f)

        logger.debug(
            "Added %d filters (%d hostname-indexed, %d generic)",
            self._total_filters,
            self._hostname_indexed,
            len(self._generic_filters) + len(self._generic_exceptions),
        )

    def _add_filter(self, f: NetworkFilter) -> None:
        """Add a single filter to the appropriate index."""
        self._total_filters += 1

        if f.is_hostname_anchor and f.hostname:
            # Index by hostname
            index = (
                self._exception_hostname_index if f.is_exception else self._hostname_index
            )
            if f.hostname not in index:
                index[f.hostname] = []
            index[f.hostname].append(f)
            self._hostname_indexed += 1
        else:
            # Generic filter
            if f.is_exception:
                self._generic_exceptions.append(f)
            else:
                self._generic_filters.append(f)

    def should_block(
        self,
        url: str,
        request_type: RequestType | None = None,
        source_hostname: str | None = None,
    ) -> MatchResult:
        """Check if a URL should be blocked.

        Args:
            url: The URL to check.
            request_type: Type of request (script, image, etc.).
            source_hostname: Hostname of the page making the request.

        Returns:
            MatchResult with blocked status and matching filter.
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.netloc.lower()

            # Remove port if present
            if ":" in hostname:
                hostname = hostname.split(":")[0]
        except Exception:
            return MatchResult(blocked=False, filter=None)

        is_third_party = _is_third_party(hostname, source_hostname)

        # First check for blocking filters
        blocking_filter = self._find_blocking_filter(
            url, hostname, request_type, source_hostname, is_third_party
        )

        if blocking_filter is None:
            return MatchResult(blocked=False, filter=None)

        # Check for exception
        exception = self._find_exception(
            url, hostname, request_type, source_hostname, is_third_party
        )

        if exception:
            return MatchResult(blocked=False, filter=exception)

        # Check for redirect
        if blocking_filter.redirect:
            return MatchResult(
                blocked=True, filter=blocking_filter, redirect=blocking_filter.redirect
            )

        return MatchResult(blocked=True, filter=blocking_filter)

    def _find_blocking_filter(
        self,
        url: str,
        hostname: str,
        request_type: RequestType | None,
        source_hostname: str | None,
        is_third_party: bool,
    ) -> NetworkFilter | None:
        """Find a blocking filter that matches."""
        # Check hostname-indexed filters first (O(1) lookup)
        for variant in _get_hostname_variants(hostname):
            filters = self._hostname_index.get(variant, [])
            for f in filters:
                if self._filter_matches(
                    f, url, hostname, request_type, source_hostname, is_third_party
                ):
                    return f

        # Check generic filters
        for f in self._generic_filters:
            if self._filter_matches(
                f, url, hostname, request_type, source_hostname, is_third_party
            ):
                return f

        return None

    def _find_exception(
        self,
        url: str,
        hostname: str,
        request_type: RequestType | None,
        source_hostname: str | None,
        is_third_party: bool,
    ) -> NetworkFilter | None:
        """Find an exception filter that matches."""
        # Check hostname-indexed exceptions
        for variant in _get_hostname_variants(hostname):
            filters = self._exception_hostname_index.get(variant, [])
            for f in filters:
                if self._filter_matches(
                    f, url, hostname, request_type, source_hostname, is_third_party
                ):
                    return f

        # Check generic exceptions
        for f in self._generic_exceptions:
            if self._filter_matches(
                f, url, hostname, request_type, source_hostname, is_third_party
            ):
                return f

        return None

    def _filter_matches(
        self,
        f: NetworkFilter,
        url: str,
        hostname: str,
        request_type: RequestType | None,
        source_hostname: str | None,
        is_third_party: bool,
    ) -> bool:
        """Check if a filter matches the request."""
        # Check third-party constraint
        if f.third_party is not None and f.third_party != is_third_party:
            return False

        # Check request type constraints
        if f.request_types and request_type not in f.request_types:
            return False
        if request_type in f.excluded_types:
            return False

        # Check domain constraints
        if source_hostname:
            if not _domain_matches(source_hostname, f.domains, f.excluded_domains):
                return False

        # Check URL pattern
        return self._pattern_matches(f, url)

    def _pattern_matches(self, f: NetworkFilter, url: str) -> bool:
        """Check if the filter pattern matches the URL."""
        # For hostname-anchored filters, we already matched hostname via index
        # Now check the full pattern
        if f.is_hostname_anchor:
            # Build the expected URL start
            hostname = f.hostname or ""
            # Check if URL contains the hostname at the right position
            try:
                parsed = urlparse(url)
                url_host = parsed.netloc.lower()
                if ":" in url_host:
                    url_host = url_host.split(":")[0]

                # Hostname must match or be a subdomain
                if url_host != hostname and not url_host.endswith("." + hostname):
                    return False
            except Exception:
                return False

        # For plain patterns, use string matching
        if f.is_plain:
            pattern = f.pattern
            if f.is_hostname_anchor and f.hostname:
                # Pattern is the part after hostname
                pattern = f.pattern[len(f.hostname) :] if f.pattern.startswith(f.hostname) else f.pattern
            return pattern.lower() in url.lower() if pattern else True

        # Use regex for complex patterns
        try:
            regex = f.get_regex()
            return regex.search(url) is not None
        except Exception:
            return False
