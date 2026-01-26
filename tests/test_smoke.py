"""
Smoke tests for webctl CLI.

These tests verify that the basic functionality works end-to-end.
Run with: uv run pytest tests/test_smoke.py -v
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Regex to strip ANSI escape codes
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_ESCAPE.sub("", text)


# Helper to run webctl commands
def run_webctl(*args, timeout=30, check=True) -> subprocess.CompletedProcess:
    """Run a webctl command and return the result."""
    cmd = [sys.executable, "-m", "webctl"] + list(args)
    # Set env to disable colors and use UTF-8 encoding
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        cwd=Path(__file__).parent.parent,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    # Strip ANSI codes from output (Typer/Rich may not respect NO_COLOR for help)
    result = subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=strip_ansi(result.stdout),
        stderr=strip_ansi(result.stderr),
    )
    if check and result.returncode != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    return result


class TestCLIBasics:
    """Test CLI loads and basic commands work without daemon."""

    def test_help(self):
        """CLI shows help without errors."""
        result = run_webctl("--help")
        assert result.returncode == 0
        assert "webctl" in result.stdout
        assert "COMMAND" in result.stdout

    def test_snapshot_help(self):
        """Snapshot command shows all options."""
        result = run_webctl("snapshot", "--help")
        assert result.returncode == 0
        assert "--interactive-only" in result.stdout
        assert "--within" in result.stdout
        assert "--limit" in result.stdout
        assert "--roles" in result.stdout

    def test_upload_help(self):
        """Upload command shows help."""
        result = run_webctl("upload", "--help")
        assert result.returncode == 0
        assert "--file" in result.stdout

    def test_wait_help(self):
        """Wait command shows available conditions."""
        result = run_webctl("wait", "--help")
        assert result.returncode == 0
        assert "network-idle" in result.stdout
        assert "exists:" in result.stdout


class TestConfig:
    """Test config commands (no daemon needed)."""

    def test_config_show(self):
        """Config show displays settings."""
        result = run_webctl("config", "show")
        assert result.returncode == 0
        assert "idle_timeout:" in result.stdout
        assert "screenshot_on_error:" in result.stdout

    def test_config_show_proxy_fields(self):
        """Config show displays proxy settings."""
        result = run_webctl("config", "show")
        assert result.returncode == 0
        assert "proxy_server:" in result.stdout
        assert "proxy_username:" in result.stdout
        assert "proxy_password:" in result.stdout
        assert "proxy_bypass:" in result.stdout

    def test_config_get(self):
        """Config get retrieves a value."""
        result = run_webctl("config", "get", "auto_start")
        assert result.returncode == 0
        assert result.stdout.strip() in ("True", "False")

    def test_config_get_proxy_server(self):
        """Config get retrieves proxy_server."""
        result = run_webctl("config", "get", "proxy_server")
        assert result.returncode == 0
        # Value is either null or a proxy URL
        assert result.stdout.strip() in ("null",) or "http" in result.stdout

    def test_config_get_invalid_key(self):
        """Config get fails for invalid key."""
        result = run_webctl("config", "get", "invalid_key", check=False)
        assert result.returncode != 0
        assert "Unknown key" in result.stdout or "Unknown key" in result.stderr


@pytest.fixture(scope="class")
def browser_session():
    """Start a browser session for tests, cleanup after."""
    # Stop any existing daemon first
    run_webctl("stop", "--daemon", check=False)
    time.sleep(0.5)

    # Start fresh session - use headless mode on CI
    start_args = ["start"]
    if os.environ.get("CI"):
        start_args.extend(["--mode", "unattended"])

    result = run_webctl(*start_args, timeout=30)
    assert result.returncode == 0, f"Failed to start: {result.stderr}"

    yield

    # Cleanup
    run_webctl("stop", "--daemon", check=False)


@pytest.mark.usefixtures("browser_session")
class TestBrowserInteraction:
    """Test browser interaction commands."""

    def test_navigate(self):
        """Navigate to a URL."""
        result = run_webctl("navigate", "https://example.com")
        assert result.returncode == 0
        assert "Example Domain" in result.stdout or "example.com" in result.stdout

    def test_snapshot(self):
        """Take a snapshot."""
        result = run_webctl("snapshot", "--limit", "10")
        assert result.returncode == 0
        # Should have some output
        assert len(result.stdout) > 0

    def test_snapshot_interactive_only(self):
        """Snapshot with interactive-only filter."""
        result = run_webctl("snapshot", "--interactive-only")
        assert result.returncode == 0
        # Example.com has at least one link
        assert "link" in result.stdout.lower()

    def test_snapshot_jsonl(self):
        """Snapshot in JSONL format."""
        result = run_webctl("--format", "jsonl", "snapshot", "--limit", "5")
        assert result.returncode == 0
        # Each line should be valid JSON
        for line in result.stdout.strip().split("\n"):
            if line:
                data = json.loads(line)
                assert "type" in data

    def test_query(self):
        """Query command finds elements."""
        result = run_webctl("query", "role=link")
        assert result.returncode == 0
        # Should find the "More information" link on example.com
        assert "match_count" in result.stdout

    def test_query_typo_suggestion(self):
        """Query suggests corrections for typos."""
        result = run_webctl("query", "role=buton")  # typo
        assert result.returncode == 0
        # Should suggest "button"
        output = result.stdout.lower()
        assert "button" in output or "not found" in output

    def test_click(self):
        """Click an element."""
        # First navigate to ensure clean state
        run_webctl("navigate", "https://example.com")

        # Example.com has "More information..." link
        result = run_webctl("click", "role=link")
        assert result.returncode == 0
        assert "clicked" in result.stdout.lower() or "OK" in result.stdout

    def test_status(self):
        """Get session status."""
        result = run_webctl("status")
        assert result.returncode == 0
        assert "Session" in result.stdout or "session" in result.stdout.lower()

    def test_quiet_mode(self):
        """Quiet mode suppresses events."""
        result = run_webctl("--quiet", "navigate", "https://example.com")
        assert result.returncode == 0
        # Should not have EVENT lines
        assert "EVENT" not in result.stdout

    def test_result_only_mode(self):
        """Result-only mode outputs only final result."""
        result = run_webctl("--result-only", "--format", "jsonl", "navigate", "https://example.com")
        assert result.returncode == 0
        # Should have exactly one line with done
        lines = [line for line in result.stdout.strip().split("\n") if line]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["type"] == "done"


class TestWaitConditions:
    """Test wait conditions (requires browser session)."""

    @pytest.fixture(autouse=True)
    def setup(self, browser_session):
        """Ensure browser is on example.com."""
        run_webctl("navigate", "https://example.com")

    def test_wait_network_idle(self):
        """Wait for network idle."""
        result = run_webctl("wait", "network-idle", timeout=10)
        assert result.returncode == 0

    def test_wait_load(self):
        """Wait for load."""
        result = run_webctl("wait", "load", timeout=10)
        assert result.returncode == 0

    def test_wait_exists(self):
        """Wait for element to exist."""
        result = run_webctl("wait", 'exists:role=link name~="More"', timeout=10)
        assert result.returncode == 0

    def test_wait_url_contains(self):
        """Wait for URL to contain text."""
        result = run_webctl("wait", 'url-contains:"example.com"', timeout=10)
        assert result.returncode == 0


class TestErrorHandling:
    """Test error handling."""

    @pytest.fixture(autouse=True)
    def setup(self, browser_session):
        """Ensure browser is on example.com."""
        run_webctl("navigate", "https://example.com")

    def test_click_nonexistent(self):
        """Click on nonexistent element fails gracefully."""
        result = run_webctl("click", 'role=button name="Does Not Exist"', check=False)
        assert result.returncode != 0
        # Should have error message with suggestions
        output = result.stdout + result.stderr
        assert (
            "no_match" in output.lower()
            or "not found" in output.lower()
            or "error" in output.lower()
        )

    def test_invalid_query_syntax(self):
        """Invalid query syntax gives helpful error."""
        result = run_webctl("query", "invalid query syntax !!!", check=False)
        # Should either succeed with no matches or fail with parse error
        # Both are acceptable
        assert result.returncode == 0 or "error" in (result.stdout + result.stderr).lower()


class TestAIFeatures:
    """Test AI-friendly features for context efficiency."""

    @pytest.fixture(autouse=True)
    def setup(self, browser_session):
        """Ensure browser is on example.com."""
        run_webctl("navigate", "https://example.com")

    def test_snapshot_count(self):
        """--count returns only stats, no elements."""
        result = run_webctl("snapshot", "--count")
        assert result.returncode == 0
        # Should have element count stats
        output = result.stdout.lower()
        assert "element" in output or "total" in output
        # Should NOT have individual element output (no n1, n2, etc.)
        assert "n1" not in result.stdout

    def test_snapshot_show_query(self):
        """--show-query includes query string for each element."""
        result = run_webctl("snapshot", "--show-query", "--limit", "5")
        assert result.returncode == 0
        # Should have query syntax like role=link name~="..."
        assert "role=" in result.stdout

    def test_snapshot_grep(self):
        """--grep filters elements by pattern."""
        result = run_webctl("snapshot", "--grep", "link")
        assert result.returncode == 0
        # Output should only contain link elements
        output = result.stdout.lower()
        assert "link" in output
        # Should not have heading elements (example.com has heading)
        lines = result.stdout.strip().split("\n")
        element_lines = [line for line in lines if line.startswith("n")]
        for line in element_lines:
            assert "link" in line.lower()

    def test_snapshot_names_only(self):
        """--names-only outputs minimal info."""
        result = run_webctl("snapshot", "--names-only", "--limit", "5")
        assert result.returncode == 0
        # Should still have output
        assert len(result.stdout) > 0

    def test_status_brief(self):
        """--brief returns one-line status."""
        result = run_webctl("status", "--brief")
        assert result.returncode == 0
        # Should be one line with URL
        lines = [line for line in result.stdout.strip().split("\n") if line]
        assert len(lines) == 1
        assert "example.com" in lines[0]

    def test_click_retry(self):
        """--retry retries on success (doesn't need to fail)."""
        result = run_webctl("click", "role=link", "--retry", "1")
        assert result.returncode == 0
        # Navigate back for next tests
        run_webctl("navigate", "https://example.com")

    def test_click_wait(self):
        """--wait waits after click."""
        result = run_webctl("click", "role=link", "--wait", "load")
        assert result.returncode == 0
        # Navigate back for next tests
        run_webctl("navigate", "https://example.com")

    def test_click_retry_and_wait(self):
        """--retry and --wait work together."""
        result = run_webctl("click", "role=link", "--retry", "1", "--wait", "load")
        assert result.returncode == 0
        # Navigate back for next tests
        run_webctl("navigate", "https://example.com")

    def test_compact_format_default(self):
        """Default format is compact with summary header."""
        result = run_webctl("snapshot", "--limit", "5")
        assert result.returncode == 0
        # Should have summary header with element count
        assert "Snapshot:" in result.stdout or "element" in result.stdout.lower()

    def test_force_flag(self):
        """--force shows full output even if large."""
        result = run_webctl("--force", "snapshot")
        assert result.returncode == 0
        # Should have output
        assert len(result.stdout) > 0


class TestFillForm:
    """Test fill-form command (needs a page with form elements)."""

    @pytest.fixture(autouse=True)
    def setup(self, browser_session):
        """Navigate to a test page with form elements."""
        # example.com doesn't have forms, so we test error handling
        run_webctl("navigate", "https://example.com")

    def test_fill_form_help(self):
        """fill-form help shows usage."""
        result = run_webctl("fill-form", "--help")
        assert result.returncode == 0
        assert "fields" in result.stdout.lower() or "json" in result.stdout.lower()

    def test_fill_form_no_fields(self):
        """fill-form with empty fields returns error."""
        result = run_webctl("fill-form", "{}", check=False)
        # Empty fields should return an error
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no fields" in output.lower() or "missing" in output.lower()

    def test_fill_form_invalid_json(self):
        """fill-form with invalid JSON fails gracefully."""
        result = run_webctl("fill-form", "not json", check=False)
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "json" in output.lower() or "error" in output.lower()
