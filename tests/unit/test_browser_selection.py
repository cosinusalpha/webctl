import importlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from webctl.cli.app import check_playwright_browser

app_module = importlib.import_module("webctl.cli.app")


def _make_dummy_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _with_browsers(monkeypatch: pytest.MonkeyPatch, base: Path, revisions: list[str]) -> None:
    # Simulate Playwright browsers directory with specific revisions
    for rev in revisions:
        (base / f"chromium-{rev}").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(base))


def test_custom_browser_path_ok(tmp_path: Path) -> None:
    custom = _make_dummy_executable(tmp_path / "chrome")
    ok, msg, fixes = check_playwright_browser(custom_executable=custom, allow_global=False)
    assert ok is True
    assert str(custom) in msg
    assert not fixes


def test_custom_browser_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing-chrome"
    ok, msg, fixes = check_playwright_browser(custom_executable=missing, allow_global=False)
    assert ok is False
    assert "not found" in msg.lower()
    assert any("browser_executable_path" in f for f in fixes)


def test_global_playwright_mismatch_disallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_browsers(monkeypatch, tmp_path, ["9999"])
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    # Force expected revision different from installed
    monkeypatch.setattr(app_module, "_expected_chromium_revision", lambda: "1234")

    ok, msg, fixes = check_playwright_browser(custom_executable=None, allow_global=False)
    assert ok is False
    assert "expected rev 1234" in msg
    assert "9999" in msg
    assert any("webctl setup" in f for f in fixes)
    assert any("use_global_playwright" in f for f in fixes)


def test_global_playwright_mismatch_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_browsers(monkeypatch, tmp_path, ["9999"])
    monkeypatch.setattr(app_module, "_expected_chromium_revision", lambda: "1234")

    ok, msg, fixes = check_playwright_browser(custom_executable=None, allow_global=True)
    assert ok is True
    assert "global Playwright browser rev 9999" in msg
    assert not fixes


def test_setup_with_custom_browser_skip_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = _make_dummy_executable(tmp_path / "chrome")

    env = os.environ.copy()
    env.update(
        {
            "WEBCTL_BROWSER_PATH": str(custom),
            "NO_COLOR": "1",
            "PYTHONIOENCODING": "utf-8",
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "webctl", "setup"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=Path(__file__).resolve().parents[2],
    )

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)

    assert result.returncode == 0
    assert "ready" in result.stdout.lower() or "ready" in result.stderr.lower()
