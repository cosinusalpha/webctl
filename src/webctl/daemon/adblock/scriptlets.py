"""
Scriptlet injection for anti-adblock bypass.

Implements common uBlock Origin scriptlets that neutralize anti-adblock scripts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .parser import ScriptletFilter

logger = logging.getLogger(__name__)

# Scriptlet implementations
# These are JavaScript functions that get injected into pages
SCRIPTLETS = {
    # Abort when a property is read
    "abort-on-property-read": """
(function(prop) {
    if (!prop) return;
    var owner = window;
    var chain = prop.split('.');
    var property = chain.pop();
    for (var i = 0; i < chain.length; i++) {
        if (!(chain[i] in owner)) {
            owner[chain[i]] = {};
        }
        owner = owner[chain[i]];
    }
    Object.defineProperty(owner, property, {
        get: function() {
            throw new ReferenceError('Blocked by adblock');
        },
        set: function() {}
    });
})
""",
    # Abort when a property is written
    "abort-on-property-write": """
(function(prop) {
    if (!prop) return;
    var owner = window;
    var chain = prop.split('.');
    var property = chain.pop();
    for (var i = 0; i < chain.length; i++) {
        if (!(chain[i] in owner)) {
            owner[chain[i]] = {};
        }
        owner = owner[chain[i]];
    }
    Object.defineProperty(owner, property, {
        get: function() { return undefined; },
        set: function() {
            throw new ReferenceError('Blocked by adblock');
        }
    });
})
""",
    # Set a property to a constant value
    "set-constant": """
(function(chain, value) {
    if (!chain) return;
    var v;
    switch (value) {
        case 'undefined': v = undefined; break;
        case 'false': v = false; break;
        case 'true': v = true; break;
        case 'null': v = null; break;
        case 'noopFunc': v = function(){}; break;
        case 'trueFunc': v = function(){ return true; }; break;
        case 'falseFunc': v = function(){ return false; }; break;
        case '': v = ''; break;
        default:
            if (/^-?\\d+$/.test(value)) {
                v = parseInt(value, 10);
            } else {
                v = value;
            }
    }
    var owner = window;
    var props = chain.split('.');
    var prop = props.pop();
    for (var i = 0; i < props.length; i++) {
        if (!(props[i] in owner)) {
            owner[props[i]] = {};
        }
        owner = owner[props[i]];
    }
    try {
        Object.defineProperty(owner, prop, {
            get: function() { return v; },
            set: function() {}
        });
    } catch(e) {
        owner[prop] = v;
    }
})
""",
    # Remove a class from elements
    "remove-class": """
(function(className, selector) {
    if (!className) return;
    selector = selector || '[class]';
    var remove = function() {
        var elements = document.querySelectorAll(selector);
        for (var i = 0; i < elements.length; i++) {
            elements[i].classList.remove(className);
        }
    };
    remove();
    var observer = new MutationObserver(remove);
    observer.observe(document.documentElement, {
        childList: true,
        subtree: true
    });
})
""",
    # Prevent setTimeout with matching callback
    "no-setTimeout-if": """
(function(needle, delay) {
    var nativeSetTimeout = window.setTimeout;
    window.setTimeout = function(callback, ms) {
        var shouldBlock = false;
        if (needle) {
            var callbackStr = typeof callback === 'function' ? callback.toString() : String(callback);
            if (callbackStr.indexOf(needle) !== -1) {
                shouldBlock = true;
            }
        }
        if (delay !== undefined && delay !== '') {
            if (ms == parseInt(delay, 10)) {
                shouldBlock = shouldBlock || !needle;
            } else {
                shouldBlock = false;
            }
        }
        if (shouldBlock) {
            return 0;
        }
        return nativeSetTimeout.apply(this, arguments);
    };
})
""",
    # Prevent setInterval with matching callback
    "no-setInterval-if": """
(function(needle, delay) {
    var nativeSetInterval = window.setInterval;
    window.setInterval = function(callback, ms) {
        var shouldBlock = false;
        if (needle) {
            var callbackStr = typeof callback === 'function' ? callback.toString() : String(callback);
            if (callbackStr.indexOf(needle) !== -1) {
                shouldBlock = true;
            }
        }
        if (delay !== undefined && delay !== '') {
            if (ms == parseInt(delay, 10)) {
                shouldBlock = shouldBlock || !needle;
            } else {
                shouldBlock = false;
            }
        }
        if (shouldBlock) {
            return 0;
        }
        return nativeSetInterval.apply(this, arguments);
    };
})
""",
    # Prevent fetch with matching URL
    "no-fetch-if": """
(function(needle) {
    var nativeFetch = window.fetch;
    window.fetch = function(resource, init) {
        var url = resource instanceof Request ? resource.url : String(resource);
        if (needle && url.indexOf(needle) !== -1) {
            return Promise.reject(new TypeError('Blocked by adblock'));
        }
        return nativeFetch.apply(this, arguments);
    };
})
""",
    # Prevent XMLHttpRequest with matching URL
    "no-xhr-if": """
(function(needle) {
    var nativeOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        if (needle && String(url).indexOf(needle) !== -1) {
            throw new Error('Blocked by adblock');
        }
        return nativeOpen.apply(this, arguments);
    };
})
""",
    # Prevent addEventListener for specific events
    "addEventListener-defuser": """
(function(type, needle) {
    var nativeAddEventListener = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(eventType, callback, options) {
        if (type && eventType === type) {
            if (!needle) {
                return;
            }
            var callbackStr = typeof callback === 'function' ? callback.toString() : '';
            if (callbackStr.indexOf(needle) !== -1) {
                return;
            }
        }
        return nativeAddEventListener.apply(this, arguments);
    };
})
""",
    # JSON prune - remove properties from JSON.parse results
    "json-prune": """
(function(props) {
    if (!props) return;
    var propList = props.split(' ');
    var nativeParse = JSON.parse;
    JSON.parse = function(text, reviver) {
        var result = nativeParse.call(this, text, reviver);
        if (result && typeof result === 'object') {
            for (var i = 0; i < propList.length; i++) {
                var prop = propList[i];
                var path = prop.split('.');
                var obj = result;
                for (var j = 0; j < path.length - 1; j++) {
                    obj = obj[path[j]];
                    if (!obj || typeof obj !== 'object') break;
                }
                if (obj && typeof obj === 'object') {
                    delete obj[path[path.length - 1]];
                }
            }
        }
        return result;
    };
})
""",
    # Nowebrtc - block WebRTC
    "nowebrtc": """
(function() {
    var rtc = window.RTCPeerConnection || window.webkitRTCPeerConnection;
    if (rtc) {
        window.RTCPeerConnection = function() {
            throw new Error('WebRTC blocked by adblock');
        };
        window.webkitRTCPeerConnection = window.RTCPeerConnection;
    }
})
""",
    # Window.name cleaner
    "window.name-defuser": """
(function() {
    window.name = '';
})
""",
    # Disable alert
    "noeval-if": """
(function(needle) {
    var nativeEval = window.eval;
    window.eval = function(code) {
        if (needle && String(code).indexOf(needle) !== -1) {
            return;
        }
        return nativeEval.apply(this, arguments);
    };
})
""",
}

# Aliases for scriptlet names
SCRIPTLET_ALIASES = {
    "aopr": "abort-on-property-read",
    "aopw": "abort-on-property-write",
    "set": "set-constant",
    "rc": "remove-class",
    "nostif": "no-setTimeout-if",
    "nosiif": "no-setInterval-if",
    "aeld": "addEventListener-defuser",
    "nano-setInterval-booster": "no-setInterval-if",
    "nano-setTimeout-booster": "no-setTimeout-if",
}


def _get_hostname_variants(hostname: str) -> list[str]:
    """Get all variants of a hostname for matching."""
    parts = hostname.split(".")
    variants = []
    for i in range(len(parts)):
        variants.append(".".join(parts[i:]))
    return variants


def _domain_matches(
    hostname: str, included: set[str], excluded: set[str]
) -> bool:
    """Check if hostname matches domain constraints."""
    hostname = hostname.lower()
    hostname_variants = _get_hostname_variants(hostname)

    # Check exclusions first
    for variant in hostname_variants:
        if variant in excluded:
            return False

    # If no inclusions specified, doesn't match (scriptlets are domain-specific)
    if not included:
        return False

    # Check inclusions
    return any(variant in included for variant in hostname_variants)


class ScriptletHandler:
    """Handle scriptlet injection for anti-adblock bypass."""

    def __init__(self) -> None:
        # Domain-specific scriptlets: domain -> list of filters
        self._domain_scriptlets: dict[str, list[ScriptletFilter]] = {}

        # Statistics
        self._total_scriptlets = 0

    def add_filters(self, filters: list[ScriptletFilter]) -> None:
        """Add scriptlet filters."""
        for f in filters:
            self._add_filter(f)

        logger.debug(
            "Added %d scriptlet filters",
            self._total_scriptlets,
        )

    def _add_filter(self, f: ScriptletFilter) -> None:
        """Add a single scriptlet filter."""
        self._total_scriptlets += 1

        # Index by each included domain
        for domain in f.domains:
            if domain not in self._domain_scriptlets:
                self._domain_scriptlets[domain] = []
            self._domain_scriptlets[domain].append(f)

    def get_scripts_for_domain(self, hostname: str) -> list[str]:
        """Get JavaScript code to inject for a domain.

        Args:
            hostname: The hostname to get scripts for.

        Returns:
            List of JavaScript code strings to inject.
        """
        scripts: list[str] = []
        hostname = hostname.lower()

        for variant in _get_hostname_variants(hostname):
            for f in self._domain_scriptlets.get(variant, []):
                if not _domain_matches(hostname, f.domains, f.excluded_domains):
                    continue

                # Get scriptlet implementation
                name = f.scriptlet_name
                if name in SCRIPTLET_ALIASES:
                    name = SCRIPTLET_ALIASES[name]

                impl = SCRIPTLETS.get(name)
                if impl is None:
                    logger.debug("Unknown scriptlet: %s", f.scriptlet_name)
                    continue

                # Build the script with arguments
                args_str = ", ".join(f"'{arg}'" for arg in f.args)
                script = f"({impl})({args_str});"
                scripts.append(script)

        return scripts

    async def inject_to_page(self, page: Page, hostname: str) -> None:
        """Inject scriptlets into a page.

        Args:
            page: The Playwright page to inject into.
            hostname: The hostname of the page.
        """
        scripts = self.get_scripts_for_domain(hostname)

        if not scripts:
            return

        # Combine all scripts into one
        combined = "\n".join(scripts)

        try:
            await page.add_init_script(combined)
            logger.debug("Injected %d scriptlets for %s", len(scripts), hostname)
        except Exception as e:
            logger.debug("Failed to inject scriptlets: %s", e)
