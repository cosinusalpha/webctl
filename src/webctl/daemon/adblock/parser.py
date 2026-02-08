"""
Filter syntax parser for ABP/uBlock format.

Parses network filters, cosmetic filters, and scriptlet filters from
EasyList and uBlock Origin filter lists.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger(__name__)


class FilterType(Enum):
    """Type of filter rule."""

    NETWORK_BLOCK = auto()
    NETWORK_EXCEPTION = auto()
    COSMETIC = auto()
    COSMETIC_EXCEPTION = auto()
    SCRIPTLET = auto()


class RequestType(Enum):
    """Types of requests for network filtering."""

    SCRIPT = "script"
    IMAGE = "image"
    STYLESHEET = "stylesheet"
    FONT = "font"
    MEDIA = "media"
    DOCUMENT = "document"
    SUBDOCUMENT = "subdocument"
    XMLHTTPREQUEST = "xmlhttprequest"
    WEBSOCKET = "websocket"
    OTHER = "other"


# Map resource types to Playwright resource types
REQUEST_TYPE_MAP = {
    "script": RequestType.SCRIPT,
    "image": RequestType.IMAGE,
    "stylesheet": RequestType.STYLESHEET,
    "font": RequestType.FONT,
    "media": RequestType.MEDIA,
    "document": RequestType.DOCUMENT,
    "subdocument": RequestType.SUBDOCUMENT,
    "xmlhttprequest": RequestType.XMLHTTPREQUEST,
    "xhr": RequestType.XMLHTTPREQUEST,
    "websocket": RequestType.WEBSOCKET,
    "other": RequestType.OTHER,
    "object": RequestType.OTHER,
    "ping": RequestType.OTHER,
}


@dataclass
class NetworkFilter:
    """Parsed network filter rule."""

    raw: str
    pattern: str
    is_exception: bool = False

    # Pattern matching
    is_hostname_anchor: bool = False  # ||
    is_left_anchor: bool = False  # |
    is_right_anchor: bool = False  # |
    is_plain: bool = False  # No wildcards or special chars
    is_regex: bool = False  # /regex/

    # Hostname for quick lookup
    hostname: str | None = None

    # Modifiers
    third_party: bool | None = None  # $third-party or $~third-party
    request_types: set[RequestType] = field(default_factory=set)
    excluded_types: set[RequestType] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)  # $domain=
    excluded_domains: set[str] = field(default_factory=set)
    redirect: str | None = None  # $redirect=resource

    # Compiled regex (lazy)
    _regex: re.Pattern[str] | None = None

    def get_regex(self) -> re.Pattern[str]:
        """Get compiled regex for this filter."""
        if self._regex is None:
            self._regex = _pattern_to_regex(self.pattern, self.is_regex)
        return self._regex


@dataclass
class CosmeticFilter:
    """Parsed cosmetic (element hiding) filter rule."""

    raw: str
    selector: str
    is_exception: bool = False
    domains: set[str] = field(default_factory=set)
    excluded_domains: set[str] = field(default_factory=set)


@dataclass
class ScriptletFilter:
    """Parsed scriptlet injection filter rule."""

    raw: str
    scriptlet_name: str
    args: list[str] = field(default_factory=list)
    domains: set[str] = field(default_factory=set)
    excluded_domains: set[str] = field(default_factory=set)


@dataclass
class ParsedFilters:
    """Collection of parsed filters."""

    network_filters: list[NetworkFilter] = field(default_factory=list)
    cosmetic_filters: list[CosmeticFilter] = field(default_factory=list)
    scriptlet_filters: list[ScriptletFilter] = field(default_factory=list)


def _pattern_to_regex(pattern: str, is_regex: bool) -> re.Pattern[str]:
    """Convert a filter pattern to regex."""
    if is_regex:
        # Remove surrounding slashes
        if pattern.startswith("/") and pattern.endswith("/"):
            pattern = pattern[1:-1]
        return re.compile(pattern, re.IGNORECASE)

    # Escape special regex chars except our wildcards
    escaped = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            escaped += ".*"
        elif c == "^":
            # Separator: matches end of string or non-alphanumeric, non-hyphen, non-underscore
            escaped += r"(?:[^\w\-.]|$)"
        elif c == "|":
            if i == 0:
                escaped += "^"
            elif i == len(pattern) - 1:
                escaped += "$"
            else:
                escaped += r"\|"
        elif c in r"\.+?{}[]()$":
            escaped += "\\" + c
        else:
            escaped += c
        i += 1

    return re.compile(escaped, re.IGNORECASE)


def _extract_hostname_from_pattern(pattern: str) -> str | None:
    """Extract hostname from a hostname-anchored pattern."""
    # Pattern like: ||example.com^ or ||example.com/path
    if not pattern:
        return None

    # Find end of hostname
    end = len(pattern)
    for i, c in enumerate(pattern):
        if c in ("^", "/", "*", "?", "$"):
            end = i
            break

    hostname = pattern[:end]

    # Validate it looks like a hostname
    if hostname and "." in hostname and not hostname.startswith("."):
        return hostname.lower()

    return None


def _parse_domains(domain_str: str) -> tuple[set[str], set[str]]:
    """Parse domain option value.

    Returns (included_domains, excluded_domains).
    """
    included: set[str] = set()
    excluded: set[str] = set()

    for domain in domain_str.split("|"):
        domain = domain.strip().lower()
        if not domain:
            continue
        if domain.startswith("~"):
            excluded.add(domain[1:])
        else:
            included.add(domain)

    return included, excluded


def _parse_modifiers(modifier_str: str, filter_obj: NetworkFilter) -> None:
    """Parse filter modifiers like $third-party,script,domain=example.com."""
    for modifier in modifier_str.split(","):
        modifier = modifier.strip().lower()
        if not modifier:
            continue

        # Handle negation
        negated = modifier.startswith("~")
        if negated:
            modifier = modifier[1:]

        # Domain option
        if modifier.startswith("domain="):
            domain_value = modifier[7:]
            inc, exc = _parse_domains(domain_value)
            filter_obj.domains.update(inc)
            filter_obj.excluded_domains.update(exc)
            continue

        # Redirect option
        if modifier.startswith("redirect="):
            filter_obj.redirect = modifier[9:]
            continue

        if modifier.startswith("redirect-rule="):
            filter_obj.redirect = modifier[14:]
            continue

        # Third-party option
        if modifier in ("third-party", "3p"):
            filter_obj.third_party = not negated
            continue

        if modifier in ("first-party", "1p"):
            filter_obj.third_party = negated
            continue

        # Request type options
        req_type = REQUEST_TYPE_MAP.get(modifier)
        if req_type:
            if negated:
                filter_obj.excluded_types.add(req_type)
            else:
                filter_obj.request_types.add(req_type)


def parse_network_filter(line: str) -> NetworkFilter | None:
    """Parse a network filter rule."""
    # Check for exception
    is_exception = line.startswith("@@")
    if is_exception:
        line = line[2:]

    # Check for modifiers
    modifier_str = ""
    if "$" in line:
        # Find the last $ that's not part of a regex
        # Handle regex patterns like /regex$/
        if line.startswith("/") and line.count("/") >= 2:
            last_slash = line.rfind("/")
            modifier_pos = line.find("$", last_slash)
        else:
            modifier_pos = line.rfind("$")

        if modifier_pos != -1:
            modifier_str = line[modifier_pos + 1 :]
            line = line[:modifier_pos]

    if not line:
        return None

    # Check for hostname anchor
    is_hostname_anchor = line.startswith("||")
    if is_hostname_anchor:
        line = line[2:]

    # Check for left anchor
    is_left_anchor = not is_hostname_anchor and line.startswith("|")
    if is_left_anchor:
        line = line[1:]

    # Check for right anchor
    is_right_anchor = line.endswith("|")
    if is_right_anchor:
        line = line[:-1]

    # Check for regex
    is_regex = line.startswith("/") and line.endswith("/") and len(line) > 2

    # Check if plain (no wildcards or special chars)
    is_plain = not is_regex and "*" not in line and "^" not in line

    # Extract hostname
    hostname = None
    if is_hostname_anchor:
        hostname = _extract_hostname_from_pattern(line)

    filter_obj = NetworkFilter(
        raw=line,
        pattern=line,
        is_exception=is_exception,
        is_hostname_anchor=is_hostname_anchor,
        is_left_anchor=is_left_anchor,
        is_right_anchor=is_right_anchor,
        is_plain=is_plain,
        is_regex=is_regex,
        hostname=hostname,
    )

    # Parse modifiers
    if modifier_str:
        _parse_modifiers(modifier_str, filter_obj)

    return filter_obj


def parse_cosmetic_filter(line: str) -> CosmeticFilter | None:
    """Parse a cosmetic filter rule."""
    # Check for exception
    is_exception = "#@#" in line

    if is_exception:
        sep = "#@#"
    elif "##" in line:
        sep = "##"
    else:
        return None

    parts = line.split(sep, 1)
    if len(parts) != 2:
        return None

    domain_part, selector = parts
    selector = selector.strip()

    if not selector:
        return None

    # Skip extended selectors (procedural filters) - too complex for now
    if any(
        selector.startswith(prefix)
        for prefix in [":has(", ":xpath(", ":not(", ":matches-css(", "+js("]
    ):
        return None

    # Parse domains
    included: set[str] = set()
    excluded: set[str] = set()

    if domain_part:
        for domain in domain_part.split(","):
            domain = domain.strip().lower()
            if not domain:
                continue
            if domain.startswith("~"):
                excluded.add(domain[1:])
            else:
                included.add(domain)

    return CosmeticFilter(
        raw=line,
        selector=selector,
        is_exception=is_exception,
        domains=included,
        excluded_domains=excluded,
    )


def parse_scriptlet_filter(line: str) -> ScriptletFilter | None:
    """Parse a scriptlet injection filter rule."""
    # Format: domain##+js(scriptlet-name, arg1, arg2)
    sep_idx = line.find("##+js(")
    if sep_idx == -1:
        return None

    domain_part = line[:sep_idx]
    scriptlet_part = line[sep_idx + 6 :]  # Skip "##+js("

    # Remove closing parenthesis
    if not scriptlet_part.endswith(")"):
        return None
    scriptlet_part = scriptlet_part[:-1]

    # Parse scriptlet name and args
    parts = scriptlet_part.split(",")
    if not parts:
        return None

    scriptlet_name = parts[0].strip()
    args = [arg.strip() for arg in parts[1:]]

    # Parse domains
    included: set[str] = set()
    excluded: set[str] = set()

    if domain_part:
        for domain in domain_part.split(","):
            domain = domain.strip().lower()
            if not domain:
                continue
            if domain.startswith("~"):
                excluded.add(domain[1:])
            else:
                included.add(domain)

    return ScriptletFilter(
        raw=line,
        scriptlet_name=scriptlet_name,
        args=args,
        domains=included,
        excluded_domains=excluded,
    )


def parse_filter_list(content: str) -> ParsedFilters:
    """Parse a filter list and return categorized filters."""
    result = ParsedFilters()

    for line in content.splitlines():
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith("!") or line.startswith("["):
            continue

        # Try to parse as different filter types
        if "##+js(" in line:
            # Scriptlet filter
            parsed = parse_scriptlet_filter(line)
            if parsed:
                result.scriptlet_filters.append(parsed)
        elif "##" in line or "#@#" in line:
            # Cosmetic filter
            parsed_cosmetic = parse_cosmetic_filter(line)
            if parsed_cosmetic:
                result.cosmetic_filters.append(parsed_cosmetic)
        else:
            # Network filter
            parsed_network = parse_network_filter(line)
            if parsed_network:
                result.network_filters.append(parsed_network)

    logger.debug(
        "Parsed filters: %d network, %d cosmetic, %d scriptlet",
        len(result.network_filters),
        len(result.cosmetic_filters),
        len(result.scriptlet_filters),
    )

    return result


def parse_all_filter_lists(lists: dict[str, str]) -> ParsedFilters:
    """Parse multiple filter lists and merge results."""
    result = ParsedFilters()

    for name, content in lists.items():
        logger.debug("Parsing filter list: %s", name)
        parsed = parse_filter_list(content)
        result.network_filters.extend(parsed.network_filters)
        result.cosmetic_filters.extend(parsed.cosmetic_filters)
        result.scriptlet_filters.extend(parsed.scriptlet_filters)

    return result
