"""
Filter list management for adblock.

Downloads and caches filter lists from uBlock Origin and EasyList sources,
with TTL-based cache invalidation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from webctl.config import get_data_dir

logger = logging.getLogger(__name__)

# Filter list sources
FILTER_LISTS = {
    "easylist": "https://easylist.to/easylist/easylist.txt",
    "easyprivacy": "https://easylist.to/easylist/easyprivacy.txt",
    "ublock-filters": "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/filters.txt",
    "ublock-badware": "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/badware.txt",
    "ublock-privacy": "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/privacy.txt",
    "ublock-annoyances": "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/annoyances-others.txt",
}

# Default lists to use when none are specified
DEFAULT_LISTS = ["easylist", "easyprivacy", "ublock-filters", "ublock-badware", "ublock-privacy"]

CACHE_TTL = 86400 * 7  # 7 days


def _get_cache_dir() -> Path:
    """Get the path to the adblock cache directory."""
    return get_data_dir() / "adblock"


def _get_list_cache_path(list_name: str) -> Path:
    """Get the path to a cached filter list."""
    return _get_cache_dir() / "lists" / f"{list_name}.txt"


def _get_cache_meta_path() -> Path:
    """Get the path to the cache metadata file."""
    return _get_cache_dir() / "cache_meta.json"


@dataclass
class FilterListMetadata:
    """Metadata for a cached filter list."""

    name: str
    url: str
    timestamp: float
    line_count: int


class FilterListManager:
    """Manage filter list downloads and caching."""

    def __init__(self) -> None:
        self._lists: dict[str, str] | None = None
        self._metadata: dict[str, FilterListMetadata] = {}
        self._lock = asyncio.Lock()

    async def get_filter_lists(
        self, list_names: list[str] | None = None
    ) -> dict[str, str]:
        """Get filter lists, loading from cache or fetching if needed.

        Args:
            list_names: List of filter list names to load. If None, uses DEFAULT_LISTS.

        Returns:
            Dictionary mapping list names to their content.
        """
        if list_names is None:
            list_names = DEFAULT_LISTS

        async with self._lock:
            if self._lists is not None:
                # Return only requested lists
                return {name: self._lists[name] for name in list_names if name in self._lists}

            self._load_metadata()
            lists: dict[str, str] = {}

            for name in list_names:
                if name not in FILTER_LISTS:
                    logger.warning("Unknown filter list: %s", name)
                    continue

                # Try cache first
                content = self._load_from_cache(name)
                if content is not None:
                    lists[name] = content
                else:
                    # Fetch from remote
                    content = await self._fetch_list(name)
                    if content:
                        lists[name] = content
                        self._save_to_cache(name, content)

            self._lists = lists
            return lists

    async def update_lists(self, list_names: list[str] | None = None) -> None:
        """Force update filter lists from remote."""
        if list_names is None:
            list_names = DEFAULT_LISTS

        async with self._lock:
            for name in list_names:
                if name not in FILTER_LISTS:
                    continue
                content = await self._fetch_list(name)
                if content:
                    if self._lists is None:
                        self._lists = {}
                    self._lists[name] = content
                    self._save_to_cache(name, content)

    def _load_metadata(self) -> None:
        """Load cache metadata."""
        meta_path = _get_cache_meta_path()
        if not meta_path.exists():
            return

        try:
            with open(meta_path) as f:
                data = json.load(f)

            for name, info in data.items():
                self._metadata[name] = FilterListMetadata(
                    name=info["name"],
                    url=info["url"],
                    timestamp=info["timestamp"],
                    line_count=info["line_count"],
                )
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load filter list metadata: %s", e)

    def _save_metadata(self) -> None:
        """Save cache metadata."""
        meta_path = _get_cache_meta_path()
        meta_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {}
        for name, meta in self._metadata.items():
            data[name] = {
                "name": meta.name,
                "url": meta.url,
                "timestamp": meta.timestamp,
                "line_count": meta.line_count,
            }

        try:
            with open(meta_path, "w") as f:
                json.dump(data, f)
        except OSError as e:
            logger.warning("Failed to save filter list metadata: %s", e)

    def _load_from_cache(self, name: str) -> str | None:
        """Load a filter list from cache if valid."""
        cache_path = _get_list_cache_path(name)

        if not cache_path.exists():
            return None

        # Check if cache is still valid
        meta = self._metadata.get(name)
        if meta is None:
            return None

        if time.time() - meta.timestamp > CACHE_TTL:
            logger.debug("Filter list cache expired: %s", name)
            return None

        try:
            with open(cache_path, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            logger.warning("Failed to load filter list from cache: %s", e)
            return None

    def _save_to_cache(self, name: str, content: str) -> None:
        """Save a filter list to cache."""
        cache_path = _get_list_cache_path(name)
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Update metadata
            self._metadata[name] = FilterListMetadata(
                name=name,
                url=FILTER_LISTS[name],
                timestamp=time.time(),
                line_count=content.count("\n") + 1,
            )
            self._save_metadata()

            logger.debug("Filter list cached: %s", name)
        except OSError as e:
            logger.warning("Failed to save filter list to cache: %s", e)

    async def _fetch_list(self, name: str) -> str | None:
        """Fetch a filter list from remote."""
        url = FILTER_LISTS.get(name)
        if not url:
            return None

        logger.debug("Fetching filter list: %s from %s", name, url)

        try:
            loop = asyncio.get_event_loop()

            def _do_fetch() -> str:
                ctx = ssl.create_default_context()
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "webctl/1.0"},
                )
                with urllib.request.urlopen(req, timeout=60, context=ctx) as response:
                    data: bytes = response.read()
                    return data.decode("utf-8")

            content: str = await loop.run_in_executor(None, _do_fetch)
            logger.debug("Fetched filter list: %s (%d lines)", name, content.count("\n") + 1)
            return content
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as e:
            logger.warning("Failed to fetch filter list %s: %s", name, e)
            return None


# Singleton instance
_filter_list_manager: FilterListManager | None = None


def get_filter_list_manager() -> FilterListManager:
    """Get the singleton FilterListManager instance."""
    global _filter_list_manager
    if _filter_list_manager is None:
        _filter_list_manager = FilterListManager()
    return _filter_list_manager
