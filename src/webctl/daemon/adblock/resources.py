"""
Redirect resources for anti-adblock bypass.

Provides dummy resources (empty images, scripts, etc.) that can be served
instead of blocking requests, so anti-adblock detection scripts think ads loaded.
"""

from __future__ import annotations

import base64

# Pre-generated dummy resources for $redirect
# These are served instead of blocking to bypass anti-adblock detection
RESOURCES: dict[str, tuple[bytes, str]] = {
    # 1x1 transparent GIF
    "1x1.gif": (
        base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"),
        "image/gif",
    ),
    # 2x2 transparent PNG
    "2x2.png": (
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAC0lEQVQI12NgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
        ),
        "image/png",
    ),
    # 3x2 transparent PNG
    "3x2.png": (
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAC0lEQVQI12NgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
        ),
        "image/png",
    ),
    # 32x32 transparent PNG
    "32x32.png": (
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAGklEQVRYR+3OAQEAAAQEMP7/1KhkUKsA7BkPCgCjAAACsEXLAAAAAElFTkSuQmCC"
        ),
        "image/png",
    ),
    # Empty JavaScript
    "noopjs": (
        b"(function(){})();",
        "application/javascript",
    ),
    # Empty JSONP-style callback
    "noopjson": (
        b"{}",
        "application/json",
    ),
    # Empty text
    "nooptext": (
        b"",
        "text/plain",
    ),
    # Empty HTML frame
    "noopframe": (
        b"<!DOCTYPE html><html><head></head><body></body></html>",
        "text/html",
    ),
    # Empty MP3 (very short silence)
    "noopmp3-0.1s": (
        base64.b64decode(
            "/+NIxAAAAAANIAAAAAExBTUUzLjEwMFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVQ=="
        ),
        "audio/mpeg",
    ),
    # Empty MP4 (1x1 black pixel, very short)
    "noopmp4-1s": (
        base64.b64decode(
            "AAAAHGZ0eXBNNFYgAAACAGlzb21pc28yYXZjMQAAAAhmcmVlAAAAGm1kYXQAAAKuBgX//6rcRem9"
            "5tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTU1IHIyOTE3IDBhODRkOTggLSBILjI2NC9NUEVHLTQg"
            "QVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDE4IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcv"
            "eDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9"
            "MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVm"
            "PTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6"
            "b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29r"
            "YWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFj"
            "ZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJh"
            "bWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdl"
            "aWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49MjUgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVz"
            "aD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBx"
            "cG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAwZYiE"
            "ABRRAAAAAAAWAAAA8gAAvU/yoEAAALmvAAAADAEAAAMAAAMA4E+lB3AAAAA="
        ),
        "video/mp4",
    ),
    # Google Analytics compatibility
    "google-analytics.com/analytics.js": (
        b"(function(){var a=window.GoogleAnalyticsObject='ga';window[a]=window[a]||function(){"
        b"(window[a].q=window[a].q||[]).push(arguments)};window[a].l=+new Date;})();",
        "application/javascript",
    ),
    # Google Analytics gtag
    "googletagmanager.com/gtag/js": (
        b"(function(){window.dataLayer=window.dataLayer||[];function gtag(){"
        b"dataLayer.push(arguments)};gtag('js',new Date);})();",
        "application/javascript",
    ),
    # Google Tag Manager
    "googletagmanager.com/gtm.js": (
        b"(function(){})();",
        "application/javascript",
    ),
    # Doubleclick
    "doubleclick.net/instream/ad_status.js": (
        b"(function(){})();",
        "application/javascript",
    ),
}

# Resource aliases (multiple names for same resource)
RESOURCE_ALIASES = {
    # Images
    "1x1-transparent.gif": "1x1.gif",
    "1x1.transparent.gif": "1x1.gif",
    "noop-1x1.gif": "1x1.gif",
    "2x2-transparent.png": "2x2.png",
    "2x2.transparent.png": "2x2.png",
    "noop-2x2.png": "2x2.png",
    "3x2-transparent.png": "3x2.png",
    "3x2.transparent.png": "3x2.png",
    "32x32-transparent.png": "32x32.png",
    "32x32.transparent.png": "32x32.png",
    # JavaScript
    "noop.js": "noopjs",
    "noopjs.js": "noopjs",
    "noop-vmap1.0.xml": "nooptext",
    # Media
    "noop-0.1s.mp3": "noopmp3-0.1s",
    "noopmp3": "noopmp3-0.1s",
    "noop-1s.mp4": "noopmp4-1s",
    "noopmp4": "noopmp4-1s",
    # Frames
    "noop.html": "noopframe",
    "noopframe.html": "noopframe",
    # Google
    "google-analytics_analytics.js": "google-analytics.com/analytics.js",
    "googlesyndication_adsbygoogle.js": "noopjs",
    "googletagservices_gpt.js": "noopjs",
    "scorecardresearch_beacon.js": "noopjs",
    "outbrain-widget.js": "noopjs",
    "ampproject_v0.js": "noopjs",
    "amazon_ads.js": "noopjs",
    "monkeybroker.js": "noopjs",
    "nobab.js": "noopjs",
    "nobab2.js": "noopjs",
}


def get_redirect_resource(name: str) -> tuple[bytes, str] | None:
    """Get a redirect resource by name.

    Args:
        name: Name of the resource (e.g., '1x1.gif', 'noopjs').

    Returns:
        Tuple of (content bytes, content type) or None if not found.
    """
    # Check aliases first
    if name in RESOURCE_ALIASES:
        name = RESOURCE_ALIASES[name]

    return RESOURCES.get(name)
