"""
Main CLI application for webctl.
"""

import asyncio
import io
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

# Fix Windows console encoding for Unicode support
if sys.platform == "win32":
    # Only wrap if not already wrapped (avoids breaking pytest capture)
    if hasattr(sys.stdout, "buffer") and not isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer") and not isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ..config import WebctlConfig, get_daemon_cmd, get_data_dir, resolve_browser_settings
from ..protocol.client import DaemonClient
from ..protocol.transport import SocketError
from .output import OutputFormatter, print_error, print_info, print_success

app = typer.Typer(
    name="webctl",
    help="Stateful, agent-first browser interface",
    no_args_is_help=True,
)

console = Console()

# Global options
_session: str = "default"
_format: str = "compact"
_timeout: int = 30000
_quiet: bool = False
_result_only: bool = False
_force: bool = False


def get_client() -> DaemonClient:
    """Get a daemon client."""
    return DaemonClient(_session)


async def ensure_daemon(session_id: str) -> bool:
    """Ensure the daemon is running, starting it if necessary."""
    config = WebctlConfig.load()

    # Try to connect
    client = get_client()
    try:
        await client.connect()
        await client.close()
        return True
    except (OSError, SocketError):
        pass

    if not config.auto_start:
        print_error("Daemon not running and auto_start is disabled")
        return False

    # Start daemon
    print_info(f"Starting daemon for session '{session_id}'...")
    cmd = get_daemon_cmd(session_id)

    # Capture stderr to a log file for diagnostics on failure
    log_dir = get_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon-startup.log"
    log_file = open(log_path, "w")  # noqa: SIM115

    # Start in background
    if sys.platform == "win32":
        subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )
    else:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )

    # Wait for daemon to start
    for _ in range(50):  # 5 seconds
        time.sleep(0.1)
        try:
            client = get_client()
            await client.connect()
            await client.close()
            return True
        except Exception:
            pass

    print_error("Failed to start daemon")

    # Show diagnostic info from the log
    try:
        log_file.close()
        log_content = log_path.read_text().strip()
        if log_content:
            console.print(f"[dim]Daemon log ({log_path}):[/dim]")
            # Show last 500 chars to avoid flooding the terminal
            console.print(f"[dim]{log_content[-500:]}[/dim]")
    except Exception:
        pass

    console.print()
    console.print("[dim]Troubleshooting tips:[/dim]")
    console.print("[dim]  - Run 'webctl setup' to ensure the browser is installed[/dim]")
    console.print("[dim]  - Run 'webctl doctor' to check your environment[/dim]")
    console.print(
        "[dim]  - In headless environments, use: webctl start --mode unattended[/dim]"
    )
    return False


async def run_command(command: str, args: dict[str, Any]) -> None:
    """Run a command against the daemon."""
    log_path = os.environ.get("WEBCTL_LOG")
    log_file = open(log_path, "a") if log_path else None  # noqa: SIM115
    formatter = OutputFormatter(
        format=_format, quiet=_quiet, result_only=_result_only, force=_force,
        log_file=log_file,
    )

    if log_file:
        log_file.write(f"$ {' '.join(sys.argv)}\n")

    if not await ensure_daemon(_session):
        raise typer.Exit(1)

    client = get_client()

    try:
        await client.connect()

        async for response in client.send_command(command, args):
            formatter.output(response.model_dump())

            if response.type == "error":
                raise typer.Exit(1)

    except SocketError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
    except ConnectionError as e:
        print_error(f"Connection failed: {e}")
        raise typer.Exit(1) from None
    finally:
        if log_file:
            log_file.write("\n")
            log_file.close()
        await client.close()


@app.callback()
def main(
    session: str = typer.Option("default", "--session", "-s", help="Session ID"),
    format: str = typer.Option(
        "compact",
        "--format",
        "-f",
        help="Output format: compact (default), auto, full, jsonl, json, kv",
    ),
    timeout: int = typer.Option(30000, "--timeout", "-t", help="Timeout in milliseconds"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress events, show only results"),
    result_only: bool = typer.Option(
        False, "--result-only", "-r", help="Output only the final result (no items/events)"
    ),
    force: bool = typer.Option(
        False, "--force", "-F", help="Show full output even if large (>200 elements)"
    ),
) -> None:
    """webctl - Stateful, agent-first browser interface"""
    global _session, _format, _timeout, _quiet, _result_only, _force
    _session = session
    _format = format
    _timeout = timeout
    _quiet = quiet
    _result_only = result_only
    _force = force


# === Setup and Diagnostics ===


def _playwright_browsers_dir() -> Path:
    """Location where Playwright stores browsers (mirrors Playwright defaults)."""

    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path:
        return Path(env_path).expanduser()

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "ms-playwright"

    # Linux / other Unix
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ms-playwright"


def _expected_chromium_revision() -> str | None:
    """Return the Chromium revision Playwright expects (best-effort)."""

    try:
        from playwright._repo_version import chromium  # type: ignore[attr-defined]

        return str(chromium)
    except Exception:
        return None


def _installed_chromium_revisions() -> list[tuple[str, Path]]:
    """List installed Chromium revisions as (revision, path)."""

    base = _playwright_browsers_dir()
    if not base.exists():
        return []

    installs: list[tuple[str, Path]] = []
    for candidate in base.glob("chromium-*"):
        if candidate.is_dir():
            revision = candidate.name.split("chromium-")[-1]
            installs.append((revision, candidate))

    installs.sort(key=lambda item: int(item[0]) if item[0].isdigit() else item[0])
    return installs


def check_playwright_browser(
    custom_executable: Path | None = None, allow_global: bool = False
) -> tuple[bool, str, list[str]]:
    """Check if a usable browser is available.

    Returns (ok, message, remediation_steps).
    """

    if custom_executable:
        if not custom_executable.exists():
            fixes = [
                "Set a valid path via: webctl config set browser_executable_path /path/to/chrome",
                "Or clear the override: webctl config set browser_executable_path null",
            ]
            return False, f"Custom browser not found: {custom_executable}", fixes

        return True, f"Using custom browser at {custom_executable}", []

    expected_rev = _expected_chromium_revision()
    installed = _installed_chromium_revisions()
    installed_revs = [rev for rev, _ in installed]
    found_rev = installed_revs[-1] if installed_revs else None
    browsers_dir = _playwright_browsers_dir()

    if expected_rev and expected_rev in installed_revs:
        return True, f"Chromium browser is installed (rev {expected_rev})", []

    if allow_global and found_rev:
        msg = (
            f"Using global Playwright browser rev {found_rev} (expected {expected_rev or 'unknown'}) "
            "because use_global_playwright is enabled"
        )
        return True, msg, []

    fixes = ["Run: webctl setup --force"]
    fixes.append(
        "Or allow global Playwright (version-mismatch OK): webctl config set use_global_playwright true"
    )

    if expected_rev and found_rev:
        msg = (
            f"Chromium browser mismatch: expected rev {expected_rev}, found {', '.join(installed_revs)} "
            f"in {browsers_dir}"
        )
    elif expected_rev:
        msg = f"Chromium browser missing: expected rev {expected_rev} in {browsers_dir}"
    elif found_rev:
        msg = f"Chromium browser installed at {found_rev} but expected revision is unknown"
    else:
        msg = "Chromium browser not installed"

    return False, msg, fixes


def _print_fix_list(fixes: list[str]) -> None:
    """Pretty-print remediation steps."""

    for fix in fixes:
        console.print(f"  • {fix}")


def install_playwright_browser(custom_executable: Path | None = None) -> bool:
    """Install Playwright Chromium browser."""

    if custom_executable:
        print_info("Custom browser configured; skipping Playwright-managed install.")
        return True

    print_info("Installing Chromium browser (this may take a few minutes)...")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=False,  # Show output to user
            timeout=600,  # 10 minutes max
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print_error("Browser installation timed out")
        return False
    except Exception as e:
        print_error(f"Browser installation failed: {e}")
        return False


def install_system_deps() -> bool:
    """Install system dependencies on Linux."""
    if sys.platform != "linux":
        return True

    print_info("Installing system dependencies...")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install-deps", "chromium"],
            capture_output=False,
            timeout=300,
        )
        return result.returncode == 0
    except Exception as e:
        print_error(f"Failed to install system dependencies: {e}")
        print_info("You may need to run: sudo playwright install-deps chromium")
        return False


@app.command("setup")
def cmd_setup(
    force: bool = typer.Option(
        False, "--force", "-f", help="Force reinstall even if already installed"
    ),
) -> None:
    """Set up webctl: install browser and dependencies.

    Run this once after installing webctl to ensure the browser is ready.
    This command will:
    - Check if Chromium is installed
    - Install Chromium if missing
    - Install system dependencies on Linux

    Examples:
        webctl setup           # Install if needed
        webctl setup --force   # Force reinstall
    """
    console.print("[bold]webctl setup[/bold]")
    console.print()

    custom_path, allow_global = resolve_browser_settings()

    # Check current status
    browser_ok, browser_msg, fixes = check_playwright_browser(custom_path, allow_global)

    if browser_ok and not force:
        print_success(f"✓ {browser_msg}")
        print_success("webctl is ready to use!")
        return

    if not browser_ok:
        console.print(f"[yellow]![/yellow] {browser_msg}")
        if fixes:
            console.print("  Fix options:")
            _print_fix_list(fixes)
        if custom_path:
            # Install cannot fix a bad custom path
            raise typer.Exit(1)

    if custom_path:
        console.print()
        console.print(
            "[bold]Custom browser configured; skipping Playwright-managed install.[/bold]"
        )
        print_success("webctl is ready to use!")
        return

    # Install system deps first on Linux
    if sys.platform == "linux":
        console.print()
        console.print("[bold]Installing system dependencies...[/bold]")
        if not install_system_deps():
            console.print("[yellow]Warning: System deps may not be fully installed[/yellow]")
            console.print("You may need to run manually: sudo playwright install-deps chromium")

    # Install browser
    console.print()
    console.print("[bold]Installing Chromium browser...[/bold]")

    if install_playwright_browser(custom_path):
        print_success("✓ Chromium browser installed successfully")
        console.print()
        print_success("webctl is ready to use!")
        console.print()
        console.print("Try it out:")
        console.print('  webctl navigate "https://example.com"')
        console.print("  webctl click @e1")
        console.print("  webctl stop")
    else:
        print_error("Failed to install browser")
        console.print()
        console.print("Try running manually:")
        console.print("  playwright install chromium")
        raise typer.Exit(1)


@app.command("doctor")
def cmd_doctor() -> None:
    """Diagnose webctl installation and show status.

    Checks:
    - Python version
    - Playwright installation
    - Browser installation
    - System dependencies (Linux)
    """
    console.print("[bold]webctl doctor[/bold]")
    console.print()

    issues = []

    # Python version
    py_version = sys.version_info
    if py_version >= (3, 11):
        console.print(
            f"[green]✓[/green] Python {py_version.major}.{py_version.minor}.{py_version.micro}"
        )
    else:
        console.print(f"[red]✗[/red] Python {py_version.major}.{py_version.minor} (need 3.11+)")
        issues.append("Upgrade Python to 3.11 or later")

    # Playwright
    import importlib.util

    if importlib.util.find_spec("playwright"):
        console.print("[green]✓[/green] Playwright installed")
    else:
        console.print("[red]✗[/red] Playwright not installed")
        issues.append("Run: pip install playwright")

    # Browser
    custom_path, allow_global = resolve_browser_settings()
    browser_ok, browser_msg, fixes = check_playwright_browser(custom_path, allow_global)
    if browser_ok:
        console.print(f"[green]✓[/green] {browser_msg}")
    else:
        console.print(f"[red]✗[/red] {browser_msg}")
        if fixes:
            _print_fix_list(fixes)
            issues.extend(fixes)
        else:
            issues.append("Run: webctl setup")

    # Config
    from ..config import get_config_dir, get_data_dir

    console.print(f"[dim]  Config: {get_config_dir()}[/dim]")
    console.print(f"[dim]  Data: {get_data_dir()}[/dim]")

    console.print()
    if issues:
        console.print("[bold red]Issues found:[/bold red]")
        for issue in issues:
            console.print(f"  • {issue}")
        raise typer.Exit(1)
    else:
        print_success("All checks passed! webctl is ready to use.")


# Lean prompt for non-skill agents (Gemini, Copilot, Codex, claude-noskill)
# These are always in context, so they must be ~25 lines
AGENT_PROMPT = """# webctl - Browser Control

Navigate: `webctl navigate "url"` (auto-starts session) | End: `webctl stop`

## Quick Reference
- `webctl navigate "url"` - Go to URL (returns snapshot with @refs)
- `webctl navigate "url" --read` - Get readable text content
- `webctl navigate "url" --search "query"` - Search on page
- `webctl snapshot` - Re-scan page (returns @refs)
- `webctl snapshot --interactive-only` - Just buttons/links/inputs
- `webctl click @e3` or `webctl click "Submit"` - Click by @ref or text
- `webctl click "Submit" --snapshot` - Click + return new page state
- `webctl type @e2 "text"` or `webctl type "Email" "text"` - Type by @ref or text
- `webctl type "Country" "Germany"` - Auto-selects from dropdown
- `webctl do '[["type","Email","x@y.com"],["type","Password","***"],["click","Log in"]]' --snapshot`
- `webctl press Enter` | `webctl scroll down` | `webctl wait network-idle`

## Target Syntax (for click/type)
- `@e3` - Element ref from snapshot (fastest)
- `"Submit"` - Find by text description (auto-matched)
- `'role=button name~="Text"'` - Full query syntax (fallback)

Run `webctl --help` for more.
"""

# Full skill content for skill-based agents (Claude, Goose)
# These are loaded on-demand when agent needs browser automation


def _load_skill_content() -> str:
    """Load SKILL.md content from package data or repo root."""
    # Try importlib.resources first (installed package)
    try:
        from importlib.resources import files

        skill_text = (files("webctl") / "skills" / "webctl" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        if skill_text:
            return skill_text
    except Exception:
        pass

    # Try repo root (development mode)
    repo_root = Path(__file__).resolve().parents[3]
    skill_file = repo_root / "skills" / "webctl" / "SKILL.md"
    if skill_file.exists():
        return skill_file.read_text(encoding="utf-8")

    raise FileNotFoundError(
        "Could not find skills/webctl/SKILL.md. Reinstall webctl or run from the repo root."
    )


SKILL_CONTENT = _load_skill_content()



@app.command("agent-prompt")
def cmd_agent_prompt(
    format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text, json, markdown"
    ),
) -> None:
    """Output instructions for AI agents.

    Use this to get a condensed prompt that teaches an AI agent how to use webctl.
    Pipe this into your agent's context or system prompt.

    Examples:
        webctl agent-prompt                    # Plain text
        webctl agent-prompt --format json      # JSON with structured data
        webctl agent-prompt --format markdown  # Markdown formatted
    """
    if format == "json":
        import json as json_module

        data = {
            "tool": "webctl",
            "description": "Browser automation CLI for AI agents",
            "instructions": AGENT_PROMPT,
            "quick_start": [
                "webctl start",
                'webctl navigate "https://example.com"',
                "webctl snapshot --interactive-only",
                "webctl stop --daemon",
            ],
            "common_commands": {
                "start": "webctl start",
                "navigate": 'webctl navigate "URL"',
                "snapshot": "webctl snapshot --interactive-only",
                "click": "webctl click 'role=button name~=\"Text\"'",
                "type": 'webctl type \'role=textbox name~="Field"\' "value"',
                "stop": "webctl stop --daemon",
            },
        }
        print(json_module.dumps(data, indent=2))
    else:
        print(AGENT_PROMPT)


# Agent config file definitions
AGENT_CONFIGS = {
    "claude": {
        "name": "Claude Code",
        "skill": True,
        "file": Path(".claude") / "skills" / "webctl" / "SKILL.md",
        "global_file": Path.home() / ".claude" / "skills" / "webctl" / "SKILL.md",
        "description": "Claude Code skill",
    },
    "claude-noskill": {
        "name": "Claude Code (legacy)",
        "file": Path("CLAUDE.md"),
        "global_file": Path.home() / ".claude" / "CLAUDE.md",
        "description": "Claude Code project-local instructions",
    },
    "goose": {
        "name": "Goose",
        "skill": True,
        "file": Path(".agents") / "skills" / "webctl" / "SKILL.md",
        "global_file": Path.home() / ".config" / "agents" / "skills" / "webctl" / "SKILL.md",
        "description": "Goose skill (portable format)",
    },
    "gemini": {
        "name": "Gemini CLI",
        "file": Path("GEMINI.md"),
        "global_file": Path.home() / ".gemini" / "GEMINI.md",
        "description": "Google Gemini CLI",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "file": Path(".github") / "copilot-instructions.md",
        # No global_file - Copilot has fragmented global support
        "description": "GitHub Copilot",
    },
    "codex": {
        "name": "Codex CLI",
        "file": Path("AGENTS.md"),
        "global_file": Path.home() / ".codex" / "AGENTS.md",
        "description": "OpenAI Codex CLI",
    },
}

# Default agents (claude-noskill excluded from default)
DEFAULT_AGENTS = ["claude", "goose", "gemini", "copilot", "codex"]


def _file_contains_webctl(filepath: Path) -> bool:
    """Check if a file already contains webctl instructions."""
    if not filepath.exists():
        return False
    try:
        content = filepath.read_text(encoding="utf-8")
        return "webctl" in content.lower() and "browser" in content.lower()
    except Exception:
        return False


def _write_agent_config(
    filepath: Path, content: str, is_skill: bool, force: bool = False
) -> tuple[bool, str]:
    """Write webctl instructions to an agent config file.

    For skill files: Creates a new file with skill content (skills are standalone).
    For non-skill files: Appends to existing content or creates new file.

    Returns (success, message).
    """
    # Check if already has webctl instructions
    if not force and _file_contains_webctl(filepath):
        return False, "already contains webctl instructions (use --force to overwrite)"

    # Ensure parent directory exists
    filepath.parent.mkdir(parents=True, exist_ok=True)

    if is_skill:
        # Skills are standalone files - always write fresh
        try:
            filepath.write_text(content, encoding="utf-8")
            return True, "created"
        except Exception as e:
            return False, f"could not write file: {e}"
    else:
        # Non-skill files: append to existing content
        separator = "\n\n---\n\n" if filepath.exists() else ""
        existing_content = ""

        if filepath.exists():
            try:
                existing_content = filepath.read_text(encoding="utf-8")
            except Exception as e:
                return False, f"could not read existing file: {e}"

        new_content = existing_content + separator + content

        try:
            filepath.write_text(new_content, encoding="utf-8")
            if existing_content:
                return True, "appended"
            else:
                return True, "created"
        except Exception as e:
            return False, f"could not write file: {e}"


@app.command("init")
def cmd_init(
    agents: str | None = typer.Option(
        None,
        "--agents",
        "-a",
        help="Comma-separated agents: claude,goose,gemini,copilot,codex,claude-noskill",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite even if webctl instructions already exist"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be done without making changes"
    ),
    directory: str | None = typer.Option(
        None, "--dir", "-d", help="Target directory (default: current)"
    ),
    use_global: bool = typer.Option(
        False, "--global", "-g", help="Install to global config locations"
    ),
) -> None:
    """Add webctl instructions to AI agent config files.

    Creates skill files for Claude Code and Goose (on-demand activation),
    and appends lean instructions to other agents' config files.

    Supported agents:
      claude        - .claude/skills/webctl/SKILL.md (Claude Code skill)
      goose         - .agents/skills/webctl/SKILL.md (Goose skill)
      gemini        - GEMINI.md (Google Gemini CLI)
      copilot       - .github/copilot-instructions.md (GitHub Copilot)
      codex         - AGENTS.md (OpenAI Codex CLI)
      claude-noskill - CLAUDE.md (Claude Code legacy, always in context)

    Examples:
        webctl init                         # Add to all default agents (project)
        webctl init --global                # Install globally
        webctl init --agents claude         # Only Claude Code skill
        webctl init --agents claude-noskill # Legacy CLAUDE.md format
        webctl init --agents claude,gemini  # Claude skill and Gemini
        webctl init --dry-run               # Preview changes
        webctl init --dir /path/to/project  # Specific project
    """
    mode = "global" if use_global else "project"
    console.print(f"[bold]webctl init[/bold] - Adding webctl instructions ({mode})")
    console.print()

    # Validate --global and --dir are not both specified
    if use_global and directory:
        print_error("Cannot use --global and --dir together")
        raise typer.Exit(1)

    # Determine target directory for project-level installs
    target_dir = Path(directory) if directory else Path.cwd()
    if not use_global and not target_dir.is_dir():
        print_error(f"Directory not found: {target_dir}")
        raise typer.Exit(1)

    if not use_global:
        console.print(f"Target directory: [cyan]{target_dir}[/cyan]")
        console.print()

    # Parse agent selection
    if agents:
        selected = [a.strip().lower() for a in agents.split(",")]
        invalid = [a for a in selected if a not in AGENT_CONFIGS]
        if invalid:
            print_error(f"Unknown agents: {', '.join(invalid)}")
            console.print(f"Valid agents: {', '.join(AGENT_CONFIGS.keys())}")
            raise typer.Exit(1)
    else:
        selected = DEFAULT_AGENTS

    # Process each agent
    results = []
    for agent_key in selected:
        config = AGENT_CONFIGS[agent_key]
        is_skill = bool(config.get("skill", False))
        file_path = config["file"]
        assert isinstance(file_path, Path)

        # Determine file path
        if use_global:
            if "global_file" not in config:
                # Agent doesn't support global config
                if not dry_run:
                    console.print(
                        f"  [yellow]⚠[/yellow] {config['name']:20} skipped (no global config support)"
                    )
                    results.append((agent_key, False))
                else:
                    console.print(
                        f"  {config['name']:20} [yellow]skip[/yellow] (no global config support)"
                    )
                continue
            filepath = config["global_file"]
            assert isinstance(filepath, Path)
        else:
            filepath = target_dir / file_path

        # Determine content to write
        content = SKILL_CONTENT if is_skill else AGENT_PROMPT

        exists = filepath.exists()
        has_webctl = _file_contains_webctl(filepath)

        # Format path for display
        display_path = str(filepath)
        if use_global:
            # Show ~ for home directory
            try:
                display_path = "~/" + str(filepath.relative_to(Path.home()))
            except ValueError:
                pass

        if dry_run:
            if has_webctl and not force:
                status = "[yellow]skip[/yellow] (already has webctl)"
            elif is_skill:
                status = "[green]create[/green]" if not exists else "[green]overwrite[/green]"
            elif exists:
                status = "[green]append[/green]"
            else:
                status = "[green]create[/green]"
            console.print(f"  {config['name']:25} {display_path}")
            console.print(f"    {status}")
        else:
            success, message = _write_agent_config(filepath, content, is_skill, force)
            if success:
                console.print(f"  [green]✓[/green] {config['name']:20} {message}")
                console.print(f"    [dim]{display_path}[/dim]")
                results.append((agent_key, True))
            else:
                console.print(f"  [yellow]![/yellow] {config['name']:20} {message}")
                results.append((agent_key, False))

    console.print()

    if dry_run:
        console.print("[dim]Dry run - no changes made. Remove --dry-run to apply.[/dim]")
    else:
        successful = sum(1 for _, success in results if success)
        if successful > 0:
            print_success(f"Updated {successful} agent config(s)")
            console.print()
            console.print("Your AI agents will now know how to use webctl for browser automation.")
            console.print("Run [cyan]webctl setup[/cyan] to ensure the browser is installed.")


# === Session Commands ===


@app.command("start")
def cmd_start(
    mode: str | None = typer.Option(
        None, "--mode", "-m", help="Mode: attended or unattended (default: from config)"
    ),
    auto_setup: bool = typer.Option(
        True, "--auto-setup/--no-auto-setup", help="Auto-install browser if missing"
    ),
) -> None:
    """Start a browser session."""
    cfg = WebctlConfig.load()
    mode = mode or cfg.default_mode

    custom_path, allow_global = resolve_browser_settings()

    # Check if browser is installed
    if auto_setup:
        browser_ok, browser_msg, fixes = check_playwright_browser(custom_path, allow_global)
        if not browser_ok:
            console.print(f"[yellow]Browser not ready:[/yellow] {browser_msg}")
            if fixes:
                _print_fix_list(fixes)
            console.print()

            if custom_path:
                # Installing won't fix an invalid custom path
                raise typer.Exit(1)

            console.print("Running automatic setup...")
            console.print()

            if install_playwright_browser(custom_path):
                print_success("Browser installed! Starting session...")
                console.print()
            else:
                print_error("Could not install browser automatically")
                console.print("Please run: webctl setup")
                raise typer.Exit(1)

    asyncio.run(run_command("session.start", {"session": _session, "mode": mode}))


@app.command("stop")
def cmd_stop(
    keep_daemon: bool = typer.Option(
        False, "--keep-daemon", help="Keep daemon running (only close browser)"
    ),
) -> None:
    """Stop the browser session and daemon (default: closes everything)."""
    asyncio.run(run_command("session.stop", {"session": _session}))
    if not keep_daemon:
        asyncio.run(run_command("daemon.shutdown", {}))


@app.command("status")
def cmd_status(
    brief: bool = typer.Option(
        False, "--brief", "-b", help="One-line summary (URL | elements | errors | state)"
    ),
) -> None:
    """Get session status.

    Examples:
        webctl status          # Full status
        webctl status --brief  # One-line: URL | elements | errors | state
    """
    asyncio.run(run_command("session.status", {"session": _session, "brief": brief}))


@app.command("save")
def cmd_save() -> None:
    """Save session state (cookies, localStorage) to disk."""
    asyncio.run(run_command("session.save", {"session": _session}))


@app.command("sessions")
def cmd_sessions() -> None:
    """List available stored session profiles."""
    asyncio.run(run_command("session.profiles", {}))


@app.command("pages")
def cmd_pages() -> None:
    """List all open pages/tabs in the current session."""
    asyncio.run(run_command("session.status", {"session": _session}))


@app.command("focus")
def cmd_focus(
    page_id: str = typer.Argument(..., help="Page ID to focus (e.g., p1, p2)"),
) -> None:
    """Switch focus to a different page/tab."""
    asyncio.run(run_command("page.focus", {"session": _session, "page_id": page_id}))


@app.command("close-page")
def cmd_close_page(
    page_id: str = typer.Argument(..., help="Page ID to close (e.g., p1, p2)"),
) -> None:
    """Close a specific page/tab."""
    asyncio.run(run_command("page.close", {"session": _session, "page_id": page_id}))


# === Navigation Commands ===


@app.command("navigate")
def cmd_navigate(
    url: str = typer.Argument(..., help="URL to navigate to"),
    wait_until: str = typer.Option(
        "load", "--wait", "-w", help="Wait condition: load, domcontentloaded, networkidle"
    ),
    read: bool = typer.Option(
        False, "--read", help="Return readable text content instead of a11y snapshot"
    ),
    search: str | None = typer.Option(
        None, "--search", help="Find search box, type query, submit, return results"
    ),
) -> None:
    """Navigate to a URL. Auto-starts session. Returns snapshot with @refs.

    Examples:
        webctl navigate "https://example.com"
        webctl navigate "https://example.com" --read
        webctl navigate "https://duckduckgo.com" --search "webctl"
    """
    asyncio.run(
        run_command(
            "navigate",
            {
                "url": url,
                "wait_until": wait_until,
                "read": read,
                "search": search,
                "session": _session,
            },
        )
    )


@app.command("back")
def cmd_back() -> None:
    """Go back in history."""
    asyncio.run(run_command("back", {"session": _session}))


@app.command("forward")
def cmd_forward() -> None:
    """Go forward in history."""
    asyncio.run(run_command("forward", {"session": _session}))


@app.command("reload")
def cmd_reload() -> None:
    """Reload the current page."""
    asyncio.run(run_command("reload", {"session": _session}))


# === Observation Commands ===


@app.command("snapshot")
def cmd_snapshot(
    view: str = typer.Option("a11y", "--view", "-v", help="View type: a11y, md, dom-lite"),
    include_bbox: bool = typer.Option(False, "--bbox", help="Include bounding boxes (a11y only)"),
    include_path: bool = typer.Option(
        True, "--path/--no-path", help="Include path hints (a11y only)"
    ),
    max_depth: int | None = typer.Option(
        None, "--max-depth", "-d", help="Limit tree traversal depth (a11y only)"
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Maximum number of nodes to return (a11y only)"
    ),
    roles: str | None = typer.Option(
        None, "--roles", "-r", help="Filter to specific ARIA roles (comma-separated, a11y only)"
    ),
    interactive_only: bool = typer.Option(
        False, "--interactive-only", "-i", help="Only return interactive elements (a11y only)"
    ),
    within: str | None = typer.Option(
        None, "--within", "-w", help="Scope to elements within container (e.g., 'role=main')"
    ),
    grep: str | None = typer.Option(
        None, "--grep", "-g", help="Filter elements by pattern (regex on role+name)"
    ),
    max_name_length: int | None = typer.Option(
        None, "--max-name-length", help="Truncate long names (default: no limit)"
    ),
    names_only: bool = typer.Option(
        False, "--names-only", "-n", help="Only output role and name (no states/attributes)"
    ),
    visible_only: bool = typer.Option(
        False, "--visible-only", help="Filter to viewport-visible elements only (slow - uses bbox)"
    ),
    show_query: bool = typer.Option(
        False, "--show-query", "-Q", help="Show the query string to target each element"
    ),
    count_only: bool = typer.Option(
        False, "--count", "-c", help="Only output element counts, no elements (zero context cost)"
    ),
    read: bool = typer.Option(
        False, "--read", help="Return readable text content (markdown) instead of a11y tree"
    ),
) -> None:
    """Take a snapshot of the current page. Returns @refs by default.

    Default shows all elements with @refs. Use --interactive-only for just interactive.

    Examples:
        webctl snapshot                          # All elements with @refs
        webctl snapshot --interactive-only       # Only buttons/links/inputs with @refs
        webctl snapshot --read                   # Readable text content
        webctl snapshot --count                  # Just counts (zero context)
        webctl snapshot --grep "button|submit"   # Filter by pattern
    """
    asyncio.run(
        run_command(
            "snapshot",
            {
                "view": view,
                "include_bbox": include_bbox,
                "include_path_hint": include_path,
                "max_depth": max_depth,
                "limit": limit,
                "roles": roles,
                "interactive_only": interactive_only,
                "within": within,
                "grep_pattern": grep,
                "max_name_length": max_name_length,
                "names_only": names_only,
                "visible_only": visible_only,
                "show_query": show_query,
                "count_only": count_only,
                "compact_refs": True,
                "read": read,
                "session": _session,
            },
        )
    )


@app.command("screenshot")
def cmd_screenshot(
    path: str | None = typer.Option(None, "--path", "-p", help="Save to file"),
    full_page: bool = typer.Option(False, "--full", help="Capture full page"),
) -> None:
    """Take a screenshot."""
    asyncio.run(
        run_command(
            "screenshot",
            {"path": path, "full_page": full_page, "session": _session},
        )
    )


@app.command("query")
def cmd_query(
    query: str = typer.Argument(..., help="Query to debug (e.g., 'role=button name~=Submit')"),
) -> None:
    """Debug a query by showing all matches and suggestions.

    Examples:
        webctl query "role=button"
        webctl query "role=button name~=Submit"
        webctl query "role=buttonz"  # Will suggest 'button'
    """
    asyncio.run(
        run_command(
            "query",
            {"query": query, "session": _session},
        )
    )


# === Interaction Commands ===


@app.command("click")
def cmd_click(
    query: str = typer.Argument(..., help="Element: @ref, 'role=X name~=Y', or text description"),
    retry: int = typer.Option(0, "--retry", "-R", help="Number of retries on failure"),
    retry_delay: int = typer.Option(1000, "--retry-delay", help="Delay between retries in ms"),
    wait_after: str | None = typer.Option(
        None,
        "--wait",
        "-w",
        help="Wait condition after click (e.g., 'network-idle', 'exists:role=dialog')",
    ),
    snapshot: bool = typer.Option(
        False, "--snapshot", "-S", help="Return page snapshot after action"
    ),
) -> None:
    """Click an element. Accepts @ref, query, or text description.

    Examples:
        webctl click @e3
        webctl click "Submit"
        webctl click 'role=button name~="Submit"' --snapshot
        webctl click "Submit" --wait network-idle
    """
    asyncio.run(
        run_command(
            "click",
            {
                "query": query,
                "retry": retry,
                "retry_delay": retry_delay,
                "wait_after": wait_after,
                "snapshot_after": snapshot,
                "session": _session,
            },
        )
    )


@app.command("type")
def cmd_type(
    query: str = typer.Argument(..., help="Element: @ref, 'role=X name~=Y', or text description"),
    text: str = typer.Argument(..., help="Text to type (or option label for dropdowns)"),
    clear: bool = typer.Option(False, "--clear", "-c", help="Clear field first"),
    submit: bool = typer.Option(False, "--submit", help="Press Enter after typing"),
    retry: int = typer.Option(0, "--retry", "-R", help="Number of retries on failure"),
    retry_delay: int = typer.Option(1000, "--retry-delay", help="Delay between retries in ms"),
    wait_after: str | None = typer.Option(
        None, "--wait", "-w", help="Wait condition after typing (e.g., 'network-idle')"
    ),
    snapshot: bool = typer.Option(
        False, "--snapshot", "-S", help="Return page snapshot after action"
    ),
) -> None:
    """Type text into an element. Auto-detects dropdowns and checkboxes.

    Examples:
        webctl type @e2 "user@example.com"
        webctl type "Email" "user@example.com"
        webctl type "Country" "Germany"          # auto-selects from dropdown
        webctl type "Search" "query" --submit --snapshot
    """
    asyncio.run(
        run_command(
            "type",
            {
                "query": query,
                "text": text,
                "clear": clear,
                "submit": submit,
                "retry": retry,
                "retry_delay": retry_delay,
                "wait_after": wait_after,
                "snapshot_after": snapshot,
                "session": _session,
            },
        )
    )


@app.command("scroll")
def cmd_scroll(
    direction: str = typer.Argument("down", help="Direction: up, down"),
    amount: int = typer.Option(300, "--amount", "-a", help="Scroll amount in pixels"),
    query: str | None = typer.Option(None, "--to", "-t", help="Scroll element into view"),
    snapshot: bool = typer.Option(
        False, "--snapshot", "-S", help="Return page snapshot after scrolling"
    ),
) -> None:
    """Scroll the page."""
    asyncio.run(
        run_command(
            "scroll",
            {
                "direction": direction,
                "amount": amount,
                "query": query,
                "snapshot_after": snapshot,
                "session": _session,
            },
        )
    )


@app.command("press")
def cmd_press(
    key: str = typer.Argument(..., help="Key to press (e.g., Enter, Tab, Escape)"),
    snapshot: bool = typer.Option(
        False, "--snapshot", "-S", help="Return page snapshot after key press"
    ),
) -> None:
    """Press a key."""
    asyncio.run(
        run_command("press", {"key": key, "snapshot_after": snapshot, "session": _session})
    )


@app.command("select")
def cmd_select(
    query: str = typer.Argument(..., help="Query to find select/dropdown element"),
    value: str | None = typer.Option(None, "--value", "-v", help="Option value to select"),
    label: str | None = typer.Option(None, "--label", "-l", help="Option label to select"),
) -> None:
    """Select an option in a dropdown."""
    if not value and not label:
        print_error("Either --value or --label is required")
        raise typer.Exit(1)
    asyncio.run(
        run_command(
            "select",
            {"query": query, "value": value, "label": label, "session": _session},
        )
    )


@app.command("check")
def cmd_check(
    query: str = typer.Argument(..., help="Query to find checkbox/radio element"),
) -> None:
    """Check a checkbox or radio button."""
    asyncio.run(run_command("check", {"query": query, "session": _session}))


@app.command("uncheck")
def cmd_uncheck(
    query: str = typer.Argument(..., help="Query to find checkbox element"),
) -> None:
    """Uncheck a checkbox."""
    asyncio.run(run_command("uncheck", {"query": query, "session": _session}))


@app.command("upload")
def cmd_upload(
    query: str = typer.Argument(..., help="Query to find file input element"),
    file: str = typer.Option(..., "--file", "-f", help="Path to file to upload"),
) -> None:
    """Upload a file to a file input element.

    Examples:
        webctl upload 'role=button name~="Upload"' --file ./document.pdf
        webctl upload 'role=textbox name~="File"' -f ~/image.png
    """
    asyncio.run(run_command("upload", {"query": query, "file": file, "session": _session}))


@app.command("fill-form")
def cmd_fill_form(
    fields_json: str = typer.Argument(..., help="JSON object of field:value pairs"),
    within: str | None = typer.Option(
        None, "--within", "-w", help="Scope to form container (e.g., 'role=form')"
    ),
) -> None:
    """Fill multiple form fields at once.

    Fields are specified as a JSON object where keys are field names and values are:
    - String: for text inputs
    - Boolean: for checkboxes (true=check, false=uncheck)

    Examples:
        webctl fill-form '{"Email": "user@example.com", "Password": "secret"}'
        webctl fill-form '{"Email": "x@y.com", "Remember me": true}' --within "role=form"
    """
    import json

    try:
        fields = json.loads(fields_json)
    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON: {e}")
        raise typer.Exit(1) from None

    if not isinstance(fields, dict):
        print_error("Fields must be a JSON object (dictionary)")
        raise typer.Exit(1)

    asyncio.run(
        run_command(
            "fill-form",
            {"fields": fields, "within": within, "session": _session},
        )
    )


@app.command("do")
def cmd_do(
    actions_json: str = typer.Argument(
        ..., help='JSON array of actions: [["click","Submit"],["type","Email","user@test.com"]]'
    ),
    snapshot: bool = typer.Option(
        False, "--snapshot", "-S", help="Return page snapshot after all actions"
    ),
) -> None:
    """Execute multiple actions in one call.

    Actions are a JSON array of [action, target, value?] tuples.
    Supported actions: click, type, press, scroll, wait.

    Examples:
        webctl do '[["type","Email","user@test.com"],["type","Password","secret"],["click","Log in"]]'
        webctl do '[["type","Search","query"],["press","Enter"]]' --snapshot
    """
    import json

    try:
        actions = json.loads(actions_json)
    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON: {e}")
        raise typer.Exit(1) from None

    if not isinstance(actions, list):
        print_error("Actions must be a JSON array")
        raise typer.Exit(1)

    asyncio.run(
        run_command(
            "do",
            {"actions": actions, "snapshot_after": snapshot, "session": _session},
        )
    )


# === Wait Commands ===


@app.command("wait")
def cmd_wait(
    until: str = typer.Argument(..., help="Condition to wait for"),
) -> None:
    """Wait for a condition to be met.

    Available conditions:
      network-idle      Wait for network to be idle
      load              Wait for page load event
      stable            Wait for page DOM to stabilize
      exists:<query>    Wait for element to exist
      visible:<query>   Wait for element to be visible
      hidden:<query>    Wait for element to disappear
      enabled:<query>   Wait for element to be enabled
      text-contains:"x" Wait for text to appear
      url-contains:"x"  Wait for URL to contain text

    Examples:
      webctl wait network-idle
      webctl wait 'exists:role=button name~="Submit"'
      webctl wait 'url-contains:"/dashboard"'
    """
    asyncio.run(run_command("wait", {"until": until, "timeout": _timeout, "session": _session}))


# === HITL Commands ===


@app.command("prompt-secret")
def cmd_prompt_secret(
    prompt: str = typer.Option("Please enter the secret:", "--prompt", "-p", help="Prompt message"),
) -> None:
    """Wait for user to enter a secret."""
    asyncio.run(run_command("prompt-secret", {"prompt": prompt, "session": _session}))


# === Console Commands ===


@app.command("console")
def cmd_console(
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream new logs continuously"),
    level: str | None = typer.Option(
        None, "--level", "-l", help="Filter by level: log, warn, error, info, debug"
    ),
    limit: int = typer.Option(100, "--limit", "-n", help="Max logs to retrieve"),
    count: bool = typer.Option(False, "--count", "-c", help="Only show counts by level"),
) -> None:
    """Get browser console logs.

    Examples:
        webctl console                    # Get last 100 logs
        webctl console --level error      # Only errors
        webctl console --follow           # Stream new logs
        webctl console -n 50 -l warn      # Last 50 warnings
        webctl console --count            # Just show counts (LLM-friendly)
    """
    asyncio.run(
        run_command(
            "console",
            {
                "follow": follow,
                "level": level,
                "limit": limit,
                "count_only": count,
                "session": _session,
            },
        )
    )


# === Config Commands ===

# Create a subcommand group for config
config_app = typer.Typer(help="Manage webctl configuration")
app.add_typer(config_app, name="config")


@config_app.command("show")
def cmd_config_show() -> None:
    """Show all configuration settings."""

    from ..config import WebctlConfig, get_config_dir

    config = WebctlConfig.load()
    config_path = get_config_dir() / "config.json"

    print(f"Config file: {config_path}")
    print(f"  exists: {config_path.exists()}")
    print()
    print("Settings:")
    print(f"  idle_timeout: {config.idle_timeout}s")
    print(f"  auto_start: {config.auto_start}")
    print(f"  default_session: {config.default_session}")
    print(f"  default_mode: {config.default_mode}")
    print(f"  a11y_include_bbox: {config.a11y_include_bbox}")
    print(f"  a11y_include_path_hint: {config.a11y_include_path_hint}")
    print(f"  screenshot_on_error: {config.screenshot_on_error}")
    print(f"  screenshot_error_dir: {config.screenshot_error_dir or 'temp'}")
    print(
        f"  browser_executable_path: {config.browser_executable_path or 'unset (managed Playwright)'}"
    )
    print(f"  use_global_playwright: {config.use_global_playwright}")
    print(f"  mobile_emulation: {config.mobile_emulation}")
    print(f"  proxy_server: {config.proxy_server or 'unset'}")
    print(f"  proxy_username: {config.proxy_username or 'unset'}")
    # Mask password for security
    proxy_password_display = "****" if config.proxy_password else "unset"
    print(f"  proxy_password: {proxy_password_display}")
    print(f"  proxy_bypass: {config.proxy_bypass or 'unset'}")


@config_app.command("get")
def cmd_config_get(
    key: str = typer.Argument(..., help="Configuration key to get"),
) -> None:
    """Get a specific configuration value."""
    from ..config import WebctlConfig

    config = WebctlConfig.load()

    valid_keys = [
        "idle_timeout",
        "auto_start",
        "default_session",
        "default_mode",
        "a11y_include_bbox",
        "a11y_include_path_hint",
        "screenshot_on_error",
        "screenshot_error_dir",
        "browser_executable_path",
        "use_global_playwright",
        "mobile_emulation",
        "proxy_server",
        "proxy_username",
        "proxy_password",
        "proxy_bypass",
    ]

    if key not in valid_keys:
        print_error(f"Unknown key: {key}")
        print(f"Valid keys: {', '.join(valid_keys)}")
        raise typer.Exit(1)

    value = getattr(config, key)
    # Mask password for security
    if key == "proxy_password" and value:
        print("****")
    else:
        print(value if value is not None else "null")


@config_app.command("set")
def cmd_config_set(
    key: str = typer.Argument(..., help="Configuration key to set"),
    value: str = typer.Argument(..., help="Value to set"),
) -> None:
    """Set a configuration value."""
    from ..config import WebctlConfig

    config = WebctlConfig.load()

    # Type conversion based on key
    bool_keys = [
        "auto_start",
        "a11y_include_bbox",
        "a11y_include_path_hint",
        "screenshot_on_error",
        "use_global_playwright",
        "mobile_emulation",
    ]
    int_keys = ["idle_timeout"]
    nullable_str_keys = [
        "screenshot_error_dir",
        "browser_executable_path",
        "proxy_server",
        "proxy_username",
        "proxy_password",
        "proxy_bypass",
    ]

    typed_value: bool | int | str | None
    if key in bool_keys:
        typed_value = value.lower() in ("true", "1", "yes", "on")
    elif key in int_keys:
        try:
            typed_value = int(value) if value.lower() != "null" else None
        except ValueError:
            print_error(f"Invalid integer value: {value}")
            raise typer.Exit(1) from None
    elif key in nullable_str_keys:
        typed_value = value if value.lower() != "null" else None
    else:
        typed_value = value

    if key == "browser_executable_path" and typed_value:
        typed_value = str(Path(str(typed_value)).expanduser())

    valid_keys = [
        "idle_timeout",
        "auto_start",
        "default_session",
        "default_mode",
        "a11y_include_bbox",
        "a11y_include_path_hint",
        "screenshot_on_error",
        "screenshot_error_dir",
        "browser_executable_path",
        "use_global_playwright",
        "mobile_emulation",
        "proxy_server",
        "proxy_username",
        "proxy_password",
        "proxy_bypass",
    ]

    if key not in valid_keys:
        print_error(f"Unknown key: {key}")
        print(f"Valid keys: {', '.join(valid_keys)}")
        raise typer.Exit(1)

    setattr(config, key, typed_value)
    config.save()
    # Mask password in output for security
    display_value = "****" if key == "proxy_password" and typed_value else typed_value
    print_success(f"Set {key} = {display_value}")


@config_app.command("path")
def cmd_config_path() -> None:
    """Show the configuration file path."""
    from ..config import get_config_dir

    print(get_config_dir() / "config.json")


if __name__ == "__main__":
    app()
