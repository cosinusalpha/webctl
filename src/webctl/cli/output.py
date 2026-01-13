"""
Output formatting for CLI.

Supports JSONL and human-readable key-value formats.
"""

import json
import os
import sys
from typing import Any

from rich.console import Console
from rich.json import JSON
from rich.panel import Panel

# Respect NO_COLOR environment variable
_no_color = os.environ.get("NO_COLOR", "") != ""

console = Console(legacy_windows=False, no_color=_no_color)
error_console = Console(stderr=True, legacy_windows=False, no_color=_no_color)


class OutputFormatter:
    """Format output based on user preferences."""

    def __init__(
        self,
        format: str = "auto",
        color: bool = True,
        quiet: bool = False,
        result_only: bool = False,
    ):
        self.format = format
        # Respect NO_COLOR environment variable
        self.color = color and not _no_color
        self.quiet = quiet  # Suppress events
        self.result_only = result_only  # Only output done/error
        self._console = Console(force_terminal=self.color, legacy_windows=False, no_color=_no_color)

    def output(self, data: dict[str, Any]) -> None:
        """Output data in the configured format."""
        msg_type = data.get("type")

        # Apply quiet/result_only filters for all formats
        if self.quiet and msg_type == "event":
            return
        if self.result_only and msg_type not in ("done", "error"):
            return

        if self.format == "jsonl":
            self._output_jsonl(data)
        elif self.format == "json":
            self._output_json(data)
        elif self.format == "kv":
            self._output_kv(data)
        else:
            # Auto format based on content
            self._output_auto(data)

    def _output_jsonl(self, data: dict[str, Any]) -> None:
        """Output as single JSON line."""
        print(json.dumps(data))

    def _output_json(self, data: dict[str, Any]) -> None:
        """Output as formatted JSON."""
        if self.color:
            self._console.print(JSON.from_data(data))
        else:
            print(json.dumps(data, indent=2))

    def _output_kv(self, data: dict[str, Any]) -> None:
        """Output as key-value pairs."""
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            print(f"{key}: {value}")

    def _output_auto(self, data: dict[str, Any]) -> None:
        """Auto-detect best output format."""
        msg_type = data.get("type")

        # Skip events in quiet mode
        if self.quiet and msg_type == "event":
            return

        # In result_only mode, only show done/error
        if self.result_only and msg_type not in ("done", "error"):
            return

        if msg_type == "item":
            self._output_item(data)
        elif msg_type == "event":
            self._output_event(data)
        elif msg_type == "error":
            self._output_error(data)
        elif msg_type == "done":
            self._output_done(data)
        else:
            self._output_json(data)

    def _output_item(self, data: dict[str, Any]) -> None:
        """Output an item response."""
        view = data.get("view", "")
        item_data = data.get("data", data)

        if view == "a11y":
            self._output_a11y_item(item_data)
        elif view == "md":
            self._output_markdown(item_data)
        elif view == "dom-lite":
            self._output_dom_lite(item_data)
        elif view == "screenshot":
            self._output_screenshot(item_data)
        elif view == "profile":
            self._output_profile(item_data)
        elif view == "status":
            self._output_status(item_data)
        else:
            self._output_jsonl(data)

    def _output_a11y_item(self, data: dict[str, Any]) -> None:
        """Output an a11y tree item."""
        role = data.get("role", "")
        name = data.get("name", "")
        node_id = data.get("id", "")
        data.get("path_hint", "")

        # Build state indicators
        states = []
        if not data.get("enabled", True):
            states.append("disabled")
        if data.get("checked") == "true":
            states.append("checked")
        if data.get("expanded") == "true":
            states.append("expanded")
        if data.get("focused"):
            states.append("focused")
        if data.get("required"):
            states.append("required")

        state_str = f" [{', '.join(states)}]" if states else ""

        if self.color:
            self._console.print(
                f"[dim]{node_id}[/dim] [cyan]{role}[/cyan] [white]{name}[/white]{state_str}"
            )
        else:
            print(f"{node_id} {role} {name}{state_str}")

    def _output_markdown(self, data: dict[str, Any]) -> None:
        """Output markdown content."""
        content = data.get("content", "")
        title = data.get("title", "")
        url = data.get("url", "")

        if self.color:
            self._console.print(Panel(content, title=f"{title} ({url})", border_style="blue"))
        else:
            print(f"# {title}")
            print(f"URL: {url}")
            print()
            print(content)

    def _output_dom_lite(self, data: dict[str, Any]) -> None:
        """Output DOM-lite view."""
        if self.color:
            # Forms
            forms = data.get("forms", [])
            if forms:
                self._console.print("\n[bold]Forms:[/bold]")
                for form in forms:
                    self._console.print(
                        f"  Form: {form.get('name', 'unnamed')} -> {form.get('action', 'N/A')}"
                    )
                    for inp in form.get("inputs", []):
                        self._console.print(
                            f"    - {inp.get('tag')} [{inp.get('type')}] name={inp.get('name')}"
                        )

            # Tables
            tables = data.get("tables", [])
            if tables:
                self._console.print("\n[bold]Tables:[/bold]")
                for table in tables:
                    headers = table.get("headers", [])
                    rows = table.get("rows", [])
                    self._console.print(f"  Table ({len(rows)} rows): {', '.join(headers[:5])}")

            # Links
            links = data.get("links", [])
            if links:
                self._console.print(f"\n[bold]Links:[/bold] ({len(links)} total)")
                for link in links[:10]:
                    self._console.print(
                        f"  - {link.get('text', 'N/A')[:50]} -> {link.get('href', '')[:60]}"
                    )
        else:
            self._output_jsonl(data)

    def _output_screenshot(self, data: dict[str, Any]) -> None:
        """Output screenshot info."""
        if "path" in data:
            print(f"Screenshot saved to: {data['path']}")
        else:
            print(f"Screenshot (base64): {len(data.get('data', ''))} bytes")

    def _output_profile(self, data: dict[str, Any]) -> None:
        """Output a session profile."""
        name = data.get("name", "")
        has_state = data.get("has_saved_state", False)
        state_icon = "[green]●[/green]" if has_state else "[dim]○[/dim]"

        if self.color:
            self._console.print(f"  {state_icon} [bold]{name}[/bold]")
        else:
            state_str = "(saved)" if has_state else ""
            print(f"  {name} {state_str}")

    def _output_status(self, data: dict[str, Any]) -> None:
        """Output session status with pages."""
        pages = data.get("pages", [])
        session_id = data.get("session_id", "")
        mode = data.get("mode", "")

        if self.color:
            self._console.print(f"Session: [bold]{session_id}[/bold] ({mode})")
            self._console.print(f"Pages: {len(pages)}")
            for page in pages:
                active = page.get("active", False)
                icon = "[green]►[/green]" if active else " "
                page_id = page.get("page_id", "")
                url = page.get("url", "")[:60]
                page.get("kind", "tab")
                self._console.print(f"  {icon} [cyan]{page_id}[/cyan] {url}")
        else:
            print(f"Session: {session_id} ({mode})")
            print(f"Pages: {len(pages)}")
            for page in pages:
                active = "*" if page.get("active") else " "
                print(f"  {active} {page.get('page_id')} {page.get('url', '')[:60]}")

    def _output_event(self, data: dict[str, Any]) -> None:
        """Output an event."""
        event = data.get("event", "")
        payload = data.get("payload", {})

        if self.color:
            self._console.print(f"[yellow]EVENT[/yellow] {event}: {json.dumps(payload)}")
        else:
            print(f"EVENT {event}: {json.dumps(payload)}")

    def _output_error(self, data: dict[str, Any]) -> None:
        """Output an error."""
        error = data.get("error", "Unknown error")
        code = data.get("code", "")
        details = data.get("details", {})

        if self.color:
            error_console.print(f"[red]ERROR[/red] [{code}] {error}")
            # Show suggestions if available
            if details:
                suggestions = details.get("suggestions", [])
                for suggestion in suggestions:
                    error_console.print(f"  [yellow]→[/yellow] {suggestion}")
                similar = details.get("similar_elements", [])
                if similar:
                    error_console.print("  [dim]Similar elements:[/dim]")
                    for elem in similar[:5]:
                        error_console.print(
                            f"    [cyan]{elem.get('role')}[/cyan] {elem.get('name', '')[:40]}"
                        )
        else:
            print(f"ERROR [{code}] {error}", file=sys.stderr)
            if details:
                for suggestion in details.get("suggestions", []):
                    print(f"  → {suggestion}", file=sys.stderr)

    def _output_done(self, data: dict[str, Any]) -> None:
        """Output done response."""
        ok = data.get("ok", False)
        summary = data.get("summary", {})

        if ok:
            if summary:
                if self.color:
                    self._console.print(f"[green]OK[/green] {json.dumps(summary)}")
                else:
                    print(f"OK {json.dumps(summary)}")
        else:
            if self.color:
                error_console.print("[red]FAILED[/red]")
            else:
                print("FAILED", file=sys.stderr)


def print_error(message: str) -> None:
    """Print an error message."""
    error_console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]{message}[/green]")


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"[yellow]{message}[/yellow]")


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"[blue]{message}[/blue]")
