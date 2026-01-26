"""Integration tests for custom browser and global Playwright settings.

These tests verify that:
1. WEBCTL_BROWSER_PATH env var works to use a custom browser executable
2. use_global_playwright config allows using mismatched Playwright versions

Tests run natively on all platforms (Windows, Linux, macOS).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _get_playwright_chromium_path() -> Path | None:
    """Get the path to Playwright's installed Chromium executable."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from playwright.sync_api import sync_playwright; "
                "p = sync_playwright().start(); "
                "print(p.chromium.executable_path); "
                "p.stop()",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            path = Path(result.stdout.strip())
            if path.exists():
                return path
    except Exception:
        pass
    return None


def _run_webctl(
    *args: str, env_extra: dict[str, str] | None = None, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    """Run webctl command with optional extra environment variables."""
    env = os.environ.copy()
    env.update({"NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"})
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        [sys.executable, "-m", "webctl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=REPO_ROOT,
    )


@pytest.fixture
def chromium_path() -> Path:
    """Get Playwright's Chromium path, skip if not available."""
    path = _get_playwright_chromium_path()
    if path is None:
        pytest.skip("Playwright Chromium not installed")
    return path


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create isolated config/data directories to avoid affecting user config."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()

    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
        monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))

    return tmp_path


def test_custom_browser_path_env_var(chromium_path: Path, isolated_config: Path) -> None:
    """Test that WEBCTL_BROWSER_PATH env var works to use a custom browser."""
    env_extra = {"WEBCTL_BROWSER_PATH": str(chromium_path)}

    if sys.platform == "win32":
        env_extra["LOCALAPPDATA"] = str(isolated_config)
    else:
        env_extra["XDG_CONFIG_HOME"] = str(isolated_config / "config")
        env_extra["XDG_DATA_HOME"] = str(isolated_config / "data")

    # Start browser with custom path
    result = _run_webctl("start", "--mode", "unattended", env_extra=env_extra)
    if result.returncode != 0:
        print("START STDOUT:", result.stdout)
        print("START STDERR:", result.stderr)
    assert result.returncode == 0, f"Failed to start: {result.stderr}"

    try:
        # Check status
        result = _run_webctl("status", "--brief", env_extra=env_extra)
        if result.returncode != 0:
            print("STATUS STDOUT:", result.stdout)
            print("STATUS STDERR:", result.stderr)
        assert result.returncode == 0
        # Status should show URL (about:blank or similar)
        assert (
            "http" in result.stdout.lower()
            or "about:" in result.stdout.lower()
            or "blank" in result.stdout.lower()
        )
    finally:
        # Always stop the daemon
        _run_webctl("stop", "--daemon", env_extra=env_extra)


def test_global_playwright_config(chromium_path: Path, isolated_config: Path) -> None:
    """Test that use_global_playwright config allows browser to start."""
    env_extra = {}

    if sys.platform == "win32":
        env_extra["LOCALAPPDATA"] = str(isolated_config)
    else:
        env_extra["XDG_CONFIG_HOME"] = str(isolated_config / "config")
        env_extra["XDG_DATA_HOME"] = str(isolated_config / "data")

    # Set use_global_playwright to true
    result = _run_webctl("config", "set", "use_global_playwright", "true", env_extra=env_extra)
    assert result.returncode == 0, f"Failed to set config: {result.stderr}"

    # Start browser (should use global Playwright)
    result = _run_webctl("start", "--mode", "unattended", env_extra=env_extra)
    if result.returncode != 0:
        print("START STDOUT:", result.stdout)
        print("START STDERR:", result.stderr)
    assert result.returncode == 0, f"Failed to start: {result.stderr}"

    try:
        # Check status
        result = _run_webctl("status", "--brief", env_extra=env_extra)
        if result.returncode != 0:
            print("STATUS STDOUT:", result.stdout)
            print("STATUS STDERR:", result.stderr)
        assert result.returncode == 0
    finally:
        # Always stop the daemon
        _run_webctl("stop", "--daemon", env_extra=env_extra)
