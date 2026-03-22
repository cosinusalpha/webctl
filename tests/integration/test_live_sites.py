"""
Live site integration tests.

These test against real websites and require internet access.
Run with: uv run pytest tests/integration/test_live_sites.py -m live -v
Skip in CI: pytest -m "not live"
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def run_webctl(*args, timeout=30, check=True) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "webctl"] + list(args)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        cwd=Path(__file__).parent.parent.parent,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
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


@pytest.fixture(scope="module")
def live_session():
    """Start a headless browser session for live tests."""
    run_webctl("stop", check=False)
    time.sleep(0.5)

    result = run_webctl("start", "--mode", "unattended", timeout=30)
    assert result.returncode == 0, f"Failed to start: {result.stderr}"

    yield

    run_webctl("stop", check=False)


pytestmark = pytest.mark.live


@pytest.mark.usefixtures("live_session")
class TestLiveSites:
    """Live site tests -- require internet access."""

    def test_wikipedia_readability(self):
        """Wikipedia: Readability extracts article content."""
        run_webctl("navigate", "https://en.wikipedia.org/wiki/Web_scraping")
        result = run_webctl("snapshot", "--view", "md")
        assert result.returncode == 0
        assert "web scraping" in result.stdout.lower()
        assert len(result.stdout) > 500

    def test_amazon_de_cookie_and_content(self):
        """Amazon.de: cookie banner dismissed, content returned."""
        run_webctl("navigate", "https://www.amazon.de", timeout=45)
        time.sleep(2)  # Wait for cookie banner
        result = run_webctl("snapshot", "--view", "md")
        assert result.returncode == 0
        assert len(result.stdout) > 100

    def test_spiegel_de_content_extraction(self):
        """Spiegel.de: news content extracted."""
        run_webctl("navigate", "https://www.spiegel.de", timeout=45)
        result = run_webctl("snapshot", "--view", "md")
        assert result.returncode == 0
        assert len(result.stdout) > 200

    def test_github_explore_fallback(self):
        """GitHub explore: non-article page uses heuristic fallback."""
        run_webctl("navigate", "https://github.com/explore")
        result = run_webctl("snapshot", "--view", "md")
        assert result.returncode == 0
        assert len(result.stdout) > 100

    def test_mobile_emulation_active(self):
        """Unattended mode session is running."""
        result = run_webctl("status")
        assert result.returncode == 0
