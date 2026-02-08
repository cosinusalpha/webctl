"""
Rule loading and caching for Consent-O-Matic.

Downloads and caches the Consent-O-Matic rules from GitHub, with TTL-based
cache invalidation.
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

RULES_URL = "https://raw.githubusercontent.com/cavi-au/Consent-O-Matic/master/Rules.json"
RULES_CACHE_TTL = 86400 * 7  # 7 days


def _get_cache_path() -> Path:
    """Get the path to the cached rules file."""
    return get_data_dir() / "consent-o-matic" / "rules.json"


def _get_cache_meta_path() -> Path:
    """Get the path to the cache metadata file."""
    return get_data_dir() / "consent-o-matic" / "cache_meta.json"


@dataclass
class CMPDetector:
    """Detection configuration for a CMP."""

    present_matcher: dict[str, Any] | None = None
    showing_matcher: dict[str, Any] | None = None


@dataclass
class CMPMethod:
    """A method (action sequence) for a CMP."""

    name: str
    action: dict[str, Any] | None = None


@dataclass
class CMPRule:
    """Rule definition for a Cookie Management Platform."""

    name: str
    prehide_selectors: list[str]
    detect_cmp: list[CMPDetector]
    detect_popup: list[CMPDetector]
    opt_in: list[CMPMethod]
    opt_out: list[CMPMethod]
    test: list[dict[str, Any]]

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> CMPRule:
        """Create a CMPRule from a dictionary."""
        detectors = data.get("detectors", [])
        methods = data.get("methods", [])

        detect_cmp = []
        detect_popup = []
        for detector in detectors:
            if "presentMatcher" in detector:
                detect_cmp.append(CMPDetector(present_matcher=detector.get("presentMatcher")))
            if "showingMatcher" in detector:
                detect_popup.append(CMPDetector(showing_matcher=detector.get("showingMatcher")))

        opt_in = []
        opt_out = []
        for method in methods:
            method_name = method.get("name", "")
            cmp_method = CMPMethod(name=method_name, action=method.get("action"))
            if method_name in ("OPEN_OPTIONS", "DO_CONSENT", "SAVE_CONSENT"):
                opt_out.append(cmp_method)
            if method_name in ("HIDE_CMP", "OPEN_OPTIONS", "DO_CONSENT", "SAVE_CONSENT"):
                opt_in.append(cmp_method)

        return cls(
            name=name,
            prehide_selectors=data.get("prehideSelectors", []),
            detect_cmp=detect_cmp,
            detect_popup=detect_popup,
            opt_in=opt_in,
            opt_out=opt_out,
            test=data.get("test", []),
        )


class RulesManager:
    """Manage Consent-O-Matic rules with caching."""

    def __init__(self) -> None:
        self._rules: dict[str, CMPRule] | None = None
        self._raw_rules: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def get_rules(self) -> dict[str, CMPRule]:
        """Get rules, loading from cache or fetching if needed."""
        async with self._lock:
            if self._rules is not None:
                return self._rules

            # Try to load from cache first
            raw_rules = self._load_from_cache()
            if raw_rules is None:
                raw_rules = await self._fetch_rules()
                if raw_rules:
                    self._save_to_cache(raw_rules)

            if raw_rules:
                self._raw_rules = raw_rules
                self._rules = self._parse_rules(raw_rules)
            else:
                self._rules = {}

            return self._rules

    async def get_raw_rules(self) -> dict[str, Any]:
        """Get raw rules dictionary (needed for method lookup)."""
        await self.get_rules()  # Ensure rules are loaded
        return self._raw_rules or {}

    async def update_rules(self) -> None:
        """Force update rules from remote."""
        async with self._lock:
            raw_rules = await self._fetch_rules()
            if raw_rules:
                self._save_to_cache(raw_rules)
                self._raw_rules = raw_rules
                self._rules = self._parse_rules(raw_rules)

    def _load_from_cache(self) -> dict[str, Any] | None:
        """Load rules from cache if valid."""
        cache_path = _get_cache_path()
        meta_path = _get_cache_meta_path()

        if not cache_path.exists() or not meta_path.exists():
            return None

        try:
            with open(meta_path) as f:
                meta = json.load(f)

            cache_time = meta.get("timestamp", 0)
            if time.time() - cache_time > RULES_CACHE_TTL:
                logger.debug("Rules cache expired")
                return None

            with open(cache_path) as f:
                result: dict[str, Any] = json.load(f)
                return result
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load rules from cache: %s", e)
            return None

    def _save_to_cache(self, rules: dict[str, Any]) -> None:
        """Save rules to cache."""
        cache_path = _get_cache_path()
        meta_path = _get_cache_meta_path()

        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            with open(cache_path, "w") as f:
                json.dump(rules, f)

            with open(meta_path, "w") as f:
                json.dump({"timestamp": time.time()}, f)

            logger.debug("Rules cached successfully")
        except OSError as e:
            logger.warning("Failed to save rules to cache: %s", e)

    async def _fetch_rules(self) -> dict[str, Any] | None:
        """Fetch rules from GitHub."""
        try:
            # Use stdlib urllib for HTTP requests (no extra dependencies)
            loop = asyncio.get_event_loop()

            def _do_fetch() -> dict[str, Any]:
                # Create SSL context for HTTPS
                ctx = ssl.create_default_context()
                req = urllib.request.Request(
                    RULES_URL,
                    headers={"User-Agent": "webctl/1.0"},
                )
                with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                    data = response.read().decode("utf-8")
                    result: dict[str, Any] = json.loads(data)
                    return result

            result = await loop.run_in_executor(None, _do_fetch)
            return result
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            logger.warning("Failed to fetch Consent-O-Matic rules: %s", e)
            return None

    def _parse_rules(self, raw_rules: dict[str, Any]) -> dict[str, CMPRule]:
        """Parse raw rules into CMPRule objects."""
        rules = {}
        for name, data in raw_rules.items():
            try:
                rules[name] = CMPRule.from_dict(name, data)
            except Exception as e:
                logger.warning("Failed to parse rule %s: %s", name, e)
        return rules


# Singleton instance
_rules_manager: RulesManager | None = None


def get_rules_manager() -> RulesManager:
    """Get the singleton RulesManager instance."""
    global _rules_manager
    if _rules_manager is None:
        _rules_manager = RulesManager()
    return _rules_manager
