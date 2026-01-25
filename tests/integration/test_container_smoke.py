import shutil
import subprocess
from pathlib import Path

import pytest

DOCKER_IMAGE = "python:3.11-slim"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run_docker(cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    full_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{REPO_ROOT}:/app",
        "-w",
        "/app",
        DOCKER_IMAGE,
        "bash",
        "-lc",
        cmd,
    ]
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"},
    )


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_container_custom_browser_path() -> None:
    """Container smoke: use custom browser executable via WEBCTL_BROWSER_PATH."""

    script = r"""
set -euo pipefail
apt-get update -qq
apt-get install -y -qq curl ca-certificates
pip install -q --upgrade pip
pip install -q playwright
playwright install-deps chromium
playwright install chromium
pip install -q .

chrome_path=$(python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    print(p.chromium.executable_path)
PY
)
if [ -z "$chrome_path" ] || [ ! -x "$chrome_path" ]; then
    echo "No chromium executable found after playwright install: $chrome_path" >&2
    exit 1
fi
WEBCTL_BROWSER_PATH="$chrome_path" webctl start --mode unattended
webctl status --brief
webctl stop --daemon || true
"""
    result = _run_docker(script)
    if result.returncode != 0:
        print("STDOUT:\n", result.stdout)
        print("STDERR:\n", result.stderr)
        if "newuidmap" in result.stderr:
            pytest.skip("docker/podman rootless newuidmap not available in environment")
    assert result.returncode == 0
    assert "http" in result.stdout or "http" in result.stderr


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_container_global_playwright_allowed() -> None:
    """Container smoke: allow global Playwright browsers when revisions mismatch."""

    script = r"""
set -euo pipefail
apt-get update -qq
apt-get install -y -qq curl ca-certificates
pip install -q --upgrade pip
pip install -q playwright
playwright install-deps chromium
playwright install chromium
pip install -q .
webctl config set use_global_playwright true
webctl start --mode unattended
webctl status --brief
webctl stop --daemon || true
"""
    result = _run_docker(script)
    if result.returncode != 0:
        print("STDOUT:\n", result.stdout)
        print("STDERR:\n", result.stderr)
        if "newuidmap" in result.stderr:
            pytest.skip("docker/podman rootless newuidmap not available in environment")
    assert result.returncode == 0
    assert "http" in result.stdout or "http" in result.stderr
