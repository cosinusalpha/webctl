"""
Main CLI application for webctl.
"""

import asyncio
import io
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

# Fix Windows console encoding for Unicode support
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ..config import WebctlConfig, get_daemon_cmd
from ..protocol.client import DaemonClient
from ..protocol.transport import TransportType
from .output import OutputFormatter, print_error, print_info, print_success

app = typer.Typer(
    name="webctl",
    help="Stateful, agent-first browser interface",
    no_args_is_help=True,
)

console = Console()

# Global options
_session: str = "default"
_format: str = "auto"
_timeout: int = 30000
_quiet: bool = False
_result_only: bool = False


def get_client() -> DaemonClient:
    """Get a daemon client."""
    config = WebctlConfig.load()

    transport_type = None
    tcp_port = None

    if config.transport == "tcp" or sys.platform == "win32":
        transport_type = TransportType.TCP
        tcp_port = config.tcp_port

    return DaemonClient(_session, transport_type, tcp_port)


async def ensure_daemon(session_id: str) -> bool:
    """Ensure the daemon is running, starting it if necessary."""
    config = WebctlConfig.load()

    # Try to connect
    client = get_client()
    try:
        await client.connect()
        await client.close()
        return True
    except Exception:
        pass

    if not config.auto_start:
        print_error("Daemon not running and auto_start is disabled")
        return False

    # Start daemon
    print_info(f"Starting daemon for session '{session_id}'...")
    cmd = get_daemon_cmd(session_id)

    # Start in background
    if sys.platform == "win32":
        subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
    return False


async def run_command(command: str, args: dict[str, Any]) -> None:
    """Run a command against the daemon."""
    formatter = OutputFormatter(format=_format, quiet=_quiet, result_only=_result_only)

    if not await ensure_daemon(_session):
        raise typer.Exit(1)

    client = get_client()

    try:
        await client.connect()

        async for response in client.send_command(command, args):
            formatter.output(response.model_dump())

            if response.type == "error":
                raise typer.Exit(1)

    except ConnectionError as e:
        print_error(f"Connection failed: {e}")
        raise typer.Exit(1) from None
    finally:
        await client.close()


@app.callback()
def main(
    session: str = typer.Option("default", "--session", "-s", help="Session ID"),
    format: str = typer.Option(
        "auto", "--format", "-f", help="Output format: auto, jsonl, json, kv"
    ),
    timeout: int = typer.Option(30000, "--timeout", "-t", help="Timeout in milliseconds"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress events, show only results"),
    result_only: bool = typer.Option(
        False, "--result-only", "-r", help="Output only the final result (no items/events)"
    ),
) -> None:
    """webctl - Stateful, agent-first browser interface"""
    global _session, _format, _timeout, _quiet, _result_only
    _session = session
    _format = format
    _timeout = timeout
    _quiet = quiet
    _result_only = result_only


# === Setup and Diagnostics ===


def check_playwright_browser() -> tuple[bool, str]:
    """Check if Playwright Chromium browser is installed."""
    try:
        # Verify playwright is importable
        from playwright._impl._driver import compute_driver_executable

        compute_driver_executable()  # Raises if playwright not properly installed

        # Run playwright to check browser status
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # If dry-run succeeds without mentioning "will download", browser is installed
        if "will download" in result.stdout.lower() or result.returncode != 0:
            return False, "Chromium browser not installed"

        return True, "Chromium browser is installed"

    except Exception as e:
        # Fallback: try to actually check if chromium executable exists
        try:
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return False, f"Could not verify browser installation: {e}"
        except Exception:
            pass
        return False, f"Playwright not properly installed: {e}"


def install_playwright_browser() -> bool:
    """Install Playwright Chromium browser."""
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

    # Check current status
    browser_ok, browser_msg = check_playwright_browser()

    if browser_ok and not force:
        print_success(f"✓ {browser_msg}")
        print_success("webctl is ready to use!")
        return

    if not browser_ok:
        console.print(f"[yellow]![/yellow] {browser_msg}")

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

    if install_playwright_browser():
        print_success("✓ Chromium browser installed successfully")
        console.print()
        print_success("webctl is ready to use!")
        console.print()
        console.print("Try it out:")
        console.print("  webctl start")
        console.print('  webctl navigate "https://example.com"')
        console.print("  webctl snapshot --interactive-only")
        console.print("  webctl stop --daemon")
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
    browser_ok, browser_msg = check_playwright_browser()
    if browser_ok:
        console.print(f"[green]✓[/green] {browser_msg}")
    else:
        console.print(f"[red]✗[/red] {browser_msg}")
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


AGENT_PROMPT = """# webctl - Browser Control Tool

Control a browser via CLI. Start with `webctl start`, end with `webctl stop --daemon`.

## Commands
- `webctl start` - Open browser
- `webctl navigate "URL"` - Go to URL
- `webctl snapshot --interactive-only` - See clickable elements (buttons, links, inputs)
- `webctl click 'role=button name~="Text"'` - Click element
- `webctl type 'role=textbox name~="Field"' "text"` - Type into field
- `webctl type '...' "text" --submit` - Type and press Enter
- `webctl select 'role=combobox name~="..."' --label "Option"` - Select dropdown
- `webctl wait 'exists:role=button name~="..."'` - Wait for element
- `webctl stop --daemon` - Close browser

## Query Syntax
- `role=button` - By ARIA role (button, link, textbox, combobox, checkbox)
- `name~="partial"` - Partial name match (preferred)
- `name="exact"` - Exact name match

## Example: Login
```
webctl start
webctl navigate "https://site.com/login"
webctl type 'role=textbox name~="Email"' "user@example.com"
webctl type 'role=textbox name~="Password"' "pass" --submit
webctl wait 'url-contains:"/dashboard"'
```

## Tips
- Use `webctl snapshot --interactive-only` to see available elements
- Use `name~=` for partial matching (more robust)
- Use `webctl query "..."` to debug if elements not found
"""


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
        "file": "CLAUDE.md",
        "description": "Claude Code / Anthropic CLI",
    },
    "gemini": {
        "name": "Gemini CLI",
        "file": "GEMINI.md",
        "description": "Google Gemini CLI",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "file": ".github/copilot-instructions.md",
        "description": "GitHub Copilot in VS Code",
    },
    "codex": {
        "name": "Codex CLI",
        "file": "AGENTS.md",
        "description": "OpenAI Codex CLI",
    },
}


def _file_contains_webctl(filepath: Path) -> bool:
    """Check if a file already contains webctl instructions."""
    if not filepath.exists():
        return False
    try:
        content = filepath.read_text(encoding="utf-8")
        return "webctl" in content.lower() and "browser" in content.lower()
    except Exception:
        return False


def _append_to_agent_config(filepath: Path, force: bool = False) -> tuple[bool, str]:
    """Append webctl instructions to an agent config file.

    Returns (success, message).
    """
    # Check if already has webctl instructions
    if not force and _file_contains_webctl(filepath):
        return False, "already contains webctl instructions (use --force to append anyway)"

    # Ensure parent directory exists
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Prepare content to append
    separator = "\n\n---\n\n" if filepath.exists() else ""
    existing_content = ""

    if filepath.exists():
        try:
            existing_content = filepath.read_text(encoding="utf-8")
        except Exception as e:
            return False, f"could not read existing file: {e}"

    # Combine content
    new_content = existing_content + separator + AGENT_PROMPT

    try:
        filepath.write_text(new_content, encoding="utf-8")
        if existing_content:
            return True, "appended to existing file"
        else:
            return True, "created new file"
    except Exception as e:
        return False, f"could not write file: {e}"


@app.command("init")
def cmd_init(
    agents: str | None = typer.Option(
        None,
        "--agents",
        "-a",
        help="Comma-separated list of agents: claude,gemini,copilot,codex (default: all)",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Add even if webctl instructions already exist"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be done without making changes"
    ),
    directory: str | None = typer.Option(
        None, "--dir", "-d", help="Target directory (default: current)"
    ),
) -> None:
    """Add webctl instructions to AI agent config files.

    This command appends webctl usage instructions to the configuration files
    used by various AI coding assistants. It will NOT overwrite existing content -
    it safely appends to any existing configuration.

    Supported agents:
      claude   - CLAUDE.md (Claude Code / Anthropic CLI)
      gemini   - GEMINI.md (Google Gemini CLI)
      copilot  - .github/copilot-instructions.md (GitHub Copilot)
      codex    - AGENTS.md (OpenAI Codex CLI)

    Examples:
        webctl init                       # Add to all agent configs
        webctl init --agents claude       # Only Claude Code
        webctl init --agents claude,gemini  # Claude and Gemini
        webctl init --dry-run             # Preview changes
        webctl init --dir /path/to/project  # Specific project
    """
    console.print("[bold]webctl init[/bold] - Adding webctl instructions to agent configs")
    console.print()

    # Determine target directory
    target_dir = Path(directory) if directory else Path.cwd()
    if not target_dir.is_dir():
        print_error(f"Directory not found: {target_dir}")
        raise typer.Exit(1)

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
        selected = list(AGENT_CONFIGS.keys())

    # Process each agent
    results = []
    for agent_key in selected:
        config = AGENT_CONFIGS[agent_key]
        filepath = target_dir / config["file"]

        exists = filepath.exists()
        has_webctl = _file_contains_webctl(filepath)

        if dry_run:
            if has_webctl and not force:
                status = "[yellow]skip[/yellow] (already has webctl)"
            elif exists:
                status = "[green]append[/green]"
            else:
                status = "[green]create[/green]"
            console.print(f"  {config['name']:20} {config['file']:35} {status}")
        else:
            success, message = _append_to_agent_config(filepath, force)
            if success:
                console.print(f"  [green]✓[/green] {config['name']:20} {message}")
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
    mode: str = typer.Option("attended", "--mode", "-m", help="Mode: attended or unattended"),
    auto_setup: bool = typer.Option(
        True, "--auto-setup/--no-auto-setup", help="Auto-install browser if missing"
    ),
) -> None:
    """Start a browser session."""
    # Check if browser is installed
    if auto_setup:
        browser_ok, browser_msg = check_playwright_browser()
        if not browser_ok:
            console.print(f"[yellow]Browser not ready:[/yellow] {browser_msg}")
            console.print()
            console.print("Running automatic setup...")
            console.print()

            if install_playwright_browser():
                print_success("Browser installed! Starting session...")
                console.print()
            else:
                print_error("Could not install browser automatically")
                console.print("Please run: webctl setup")
                raise typer.Exit(1)

    asyncio.run(run_command("session.start", {"session": _session, "mode": mode}))


@app.command("stop")
def cmd_stop(
    daemon: bool = typer.Option(False, "--daemon", "-d", help="Also shutdown the daemon process"),
) -> None:
    """Stop the browser session (and optionally the daemon)."""
    asyncio.run(run_command("session.stop", {"session": _session}))
    if daemon:
        asyncio.run(run_command("daemon.shutdown", {}))


@app.command("status")
def cmd_status() -> None:
    """Get session status."""
    asyncio.run(run_command("session.status", {"session": _session}))


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
) -> None:
    """Navigate to a URL."""
    asyncio.run(
        run_command("navigate", {"url": url, "wait_until": wait_until, "session": _session})
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
) -> None:
    """Take a snapshot of the current page."""
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
    query: str = typer.Argument(..., help="Query to find element"),
) -> None:
    """Click an element."""
    asyncio.run(run_command("click", {"query": query, "session": _session}))


@app.command("type")
def cmd_type(
    query: str = typer.Argument(..., help="Query to find element"),
    text: str = typer.Argument(..., help="Text to type"),
    clear: bool = typer.Option(False, "--clear", "-c", help="Clear field first"),
    submit: bool = typer.Option(False, "--submit", help="Press Enter after typing"),
) -> None:
    """Type text into an element."""
    asyncio.run(
        run_command(
            "type",
            {"query": query, "text": text, "clear": clear, "submit": submit, "session": _session},
        )
    )


@app.command("scroll")
def cmd_scroll(
    direction: str = typer.Argument("down", help="Direction: up, down"),
    amount: int = typer.Option(300, "--amount", "-a", help="Scroll amount in pixels"),
    query: str | None = typer.Option(None, "--to", "-t", help="Scroll element into view"),
) -> None:
    """Scroll the page."""
    asyncio.run(
        run_command(
            "scroll",
            {"direction": direction, "amount": amount, "query": query, "session": _session},
        )
    )


@app.command("press")
def cmd_press(
    key: str = typer.Argument(..., help="Key to press (e.g., Enter, Tab, Escape)"),
) -> None:
    """Press a key."""
    asyncio.run(run_command("press", {"key": key, "session": _session}))


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
    print(f"  transport: {config.transport}")
    print(f"  tcp_host: {config.tcp_host}")
    print(f"  tcp_port: {config.tcp_port or 'auto'}")
    print(f"  idle_timeout: {config.idle_timeout}s")
    print(f"  auto_start: {config.auto_start}")
    print(f"  default_session: {config.default_session}")
    print(f"  default_mode: {config.default_mode}")
    print(f"  a11y_include_bbox: {config.a11y_include_bbox}")
    print(f"  a11y_include_path_hint: {config.a11y_include_path_hint}")
    print(f"  screenshot_on_error: {config.screenshot_on_error}")
    print(f"  screenshot_error_dir: {config.screenshot_error_dir or 'temp'}")


@config_app.command("get")
def cmd_config_get(
    key: str = typer.Argument(..., help="Configuration key to get"),
) -> None:
    """Get a specific configuration value."""
    from ..config import WebctlConfig

    config = WebctlConfig.load()

    valid_keys = [
        "transport",
        "tcp_host",
        "tcp_port",
        "idle_timeout",
        "auto_start",
        "default_session",
        "default_mode",
        "a11y_include_bbox",
        "a11y_include_path_hint",
        "screenshot_on_error",
        "screenshot_error_dir",
    ]

    if key not in valid_keys:
        print_error(f"Unknown key: {key}")
        print(f"Valid keys: {', '.join(valid_keys)}")
        raise typer.Exit(1)

    value = getattr(config, key)
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
    bool_keys = ["auto_start", "a11y_include_bbox", "a11y_include_path_hint", "screenshot_on_error"]
    int_keys = ["tcp_port", "idle_timeout"]
    nullable_str_keys = ["screenshot_error_dir"]

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

    valid_keys = [
        "transport",
        "tcp_host",
        "tcp_port",
        "idle_timeout",
        "auto_start",
        "default_session",
        "default_mode",
        "a11y_include_bbox",
        "a11y_include_path_hint",
        "screenshot_on_error",
        "screenshot_error_dir",
    ]

    if key not in valid_keys:
        print_error(f"Unknown key: {key}")
        print(f"Valid keys: {', '.join(valid_keys)}")
        raise typer.Exit(1)

    setattr(config, key, typed_value)
    config.save()
    print_success(f"Set {key} = {typed_value}")


@config_app.command("path")
def cmd_config_path() -> None:
    """Show the configuration file path."""
    from ..config import get_config_dir

    print(get_config_dir() / "config.json")


if __name__ == "__main__":
    app()
