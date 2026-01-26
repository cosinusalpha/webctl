"""
Configuration and path management for webctl.
"""

import json
import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .security.domain_policy import PolicyConfig


@dataclass
class WebctlConfig:
    """Main configuration."""

    # Daemon
    idle_timeout: int = 900  # 15 minutes
    auto_start: bool = True

    # Session defaults
    default_session: str = "default"
    default_mode: Literal["attended", "unattended"] = "attended"

    # Security
    domain_policy: PolicyConfig = field(default_factory=PolicyConfig)

    # Views
    a11y_include_bbox: bool = False
    a11y_include_path_hint: bool = True

    # Debugging
    screenshot_on_error: bool = False
    screenshot_error_dir: str | None = None  # None = use temp dir

    # Browser selection
    browser_executable_path: str | None = None  # Override to use a custom Chromium
    use_global_playwright: bool = False  # Allow global Playwright even if version mismatches

    # Proxy settings
    proxy_server: str | None = None  # e.g., "http://proxy:8080"
    proxy_username: str | None = None  # optional auth
    proxy_password: str | None = None  # optional auth
    proxy_bypass: str | None = None  # comma-separated bypass list

    @classmethod
    def load(cls, path: Path | None = None) -> "WebctlConfig":
        """Load configuration from file."""
        if path is None:
            path = get_config_dir() / "config.json"

        if not path.exists():
            return cls()

        with open(path) as f:
            data = json.load(f)

        # Warn about deprecated config keys
        deprecated = [k for k in ("transport", "tcp_host", "tcp_port") if k in data]
        if deprecated:
            warnings.warn(
                f"Config keys {deprecated} are deprecated and ignored. "
                "webctl now uses Unix sockets only.",
                DeprecationWarning,
                stacklevel=2,
            )

        return cls(
            idle_timeout=data.get("idle_timeout", 900),
            auto_start=data.get("auto_start", True),
            default_session=data.get("default_session", "default"),
            default_mode=data.get("default_mode", "attended"),
            domain_policy=PolicyConfig.from_dict(data.get("domain_policy", {})),
            a11y_include_bbox=data.get("a11y_include_bbox", False),
            a11y_include_path_hint=data.get("a11y_include_path_hint", True),
            screenshot_on_error=data.get("screenshot_on_error", False),
            screenshot_error_dir=data.get("screenshot_error_dir"),
            browser_executable_path=data.get("browser_executable_path"),
            use_global_playwright=data.get("use_global_playwright", False),
            proxy_server=data.get("proxy_server"),
            proxy_username=data.get("proxy_username"),
            proxy_password=data.get("proxy_password"),
            proxy_bypass=data.get("proxy_bypass"),
        )

    def save(self, path: Path | None = None) -> None:
        """Save configuration to file."""
        if path is None:
            path = get_config_dir() / "config.json"

        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "idle_timeout": self.idle_timeout,
            "auto_start": self.auto_start,
            "default_session": self.default_session,
            "default_mode": self.default_mode,
            "domain_policy": {
                "enabled": self.domain_policy.enabled,
                "policy": {
                    "mode": self.domain_policy.policy.mode,
                    "allow": self.domain_policy.policy.allow_patterns,
                    "deny": self.domain_policy.policy.deny_patterns,
                },
            },
            "a11y_include_bbox": self.a11y_include_bbox,
            "a11y_include_path_hint": self.a11y_include_path_hint,
            "screenshot_on_error": self.screenshot_on_error,
            "screenshot_error_dir": self.screenshot_error_dir,
            "browser_executable_path": self.browser_executable_path,
            "use_global_playwright": self.use_global_playwright,
            "proxy_server": self.proxy_server,
            "proxy_username": self.proxy_username,
            "proxy_password": self.proxy_password,
            "proxy_bypass": self.proxy_bypass,
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)


def get_config_dir() -> Path:
    """Get config directory following platform conventions."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    return base / "webctl"


def get_data_dir() -> Path:
    """Get data directory for profiles and state."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    return base / "webctl"


def get_profile_dir(session_id: str) -> Path:
    """Get profile directory for a session."""
    return get_data_dir() / "profiles" / session_id


def get_base_profile_dir() -> Path:
    """Get base directory containing all session profiles."""
    return get_data_dir() / "profiles"


def get_daemon_cmd(session_id: str) -> list[str]:
    """Get command to start daemon."""
    return [sys.executable, "-m", "webctl.daemon.server", "--session", session_id]


def resolve_browser_settings() -> tuple[Path | None, bool]:
    """Resolve browser selection preferences.

    Priority:
    1. WEBCTL_BROWSER_PATH environment variable
    2. browser_executable_path from config
    3. Default managed Playwright browser
    """

    cfg = WebctlConfig.load()

    env_path = os.environ.get("WEBCTL_BROWSER_PATH")
    if env_path:
        return Path(env_path).expanduser(), cfg.use_global_playwright

    if cfg.browser_executable_path:
        return Path(cfg.browser_executable_path).expanduser(), cfg.use_global_playwright

    return None, cfg.use_global_playwright


def resolve_proxy_settings() -> dict[str, Any] | None:
    """Resolve proxy settings for Playwright.

    Priority:
    1. WEBCTL_PROXY_SERVER environment variable
    2. HTTPS_PROXY environment variable
    3. HTTP_PROXY environment variable
    4. proxy_server from config file

    Returns a Playwright-compatible proxy dict or None if no proxy configured.
    """
    cfg = WebctlConfig.load()

    # Determine proxy server with priority chain
    proxy_server = (
        os.environ.get("WEBCTL_PROXY_SERVER")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or cfg.proxy_server
    )

    if not proxy_server:
        return None

    # Build Playwright proxy config
    proxy_config: dict[str, Any] = {"server": proxy_server}

    # Add authentication if configured (only from config file)
    if cfg.proxy_username:
        proxy_config["username"] = cfg.proxy_username
    if cfg.proxy_password:
        proxy_config["password"] = cfg.proxy_password

    # Add bypass list (from env var NO_PROXY or config)
    bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or cfg.proxy_bypass
    if bypass:
        proxy_config["bypass"] = bypass

    return proxy_config


# Default settings (RFC SS6, SS13)
DEFAULT_IDLE_TIMEOUT = 900  # 15 minutes
DEFAULT_SESSION = "default"
DEFAULT_MODE = "attended"
