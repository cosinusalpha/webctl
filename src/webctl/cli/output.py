"""
Output formatting for CLI.

Supports JSONL, compact, and human-readable key-value formats.
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

# Large output threshold for speedbump
LARGE_OUTPUT_THRESHOLD = 200


class OutputFormatter:
    """Format output based on user preferences."""

    def __init__(
        self,
        format: str = "auto",
        color: bool = True,
        quiet: bool = False,
        result_only: bool = False,
        force: bool = False,
    ):
        self.format = format
        # Respect NO_COLOR environment variable
        self.color = color and not _no_color
        self.quiet = quiet  # Suppress events
        self.result_only = result_only  # Only output done/error
        self.force = force  # Show full output even if large
        self._console = Console(force_terminal=self.color, legacy_windows=False, no_color=_no_color)
        # A11y buffering for speedbump
        self._a11y_buffer: list[dict[str, Any]] = []
        self._a11y_count = 0
        self._a11y_stats: dict[str, Any] = {}
        self._summary_printed = False

    def output(self, data: dict[str, Any]) -> None:
        """Output data in the configured format."""
        msg_type = data.get("type")

        # Apply quiet/result_only filters for all formats
        if self.quiet and msg_type == "event":
            return
        if self.result_only and msg_type not in ("done", "error"):
            return

        # For compact format with a11y view, use buffering for speedbump
        if self.format == "compact" and msg_type == "item" and data.get("view") == "a11y":
            self._buffer_a11y_item(data)
            return

        # Handle done response - flush a11y buffer if needed
        if msg_type == "done" and self._a11y_count > 0:
            self._a11y_stats = data.get("summary", {})
            self._flush_a11y_output()
            # Don't output the done message separately in compact mode
            if self.format == "compact":
                return

        if self.format == "jsonl":
            self._output_jsonl(data)
        elif self.format == "json":
            self._output_json(data)
        elif self.format == "kv":
            self._output_kv(data)
        elif self.format == "compact":
            self._output_compact(data)
        elif self.format == "full":
            # Full format is like auto but outputs all a11y details
            self._output_auto(data)
        else:
            # Auto format based on content
            self._output_auto(data)

    def _buffer_a11y_item(self, data: dict[str, Any]) -> None:
        """Buffer a11y items for speedbump logic."""
        self._a11y_count += 1
        # Only buffer up to threshold + preview (20 items)
        if self.force or self._a11y_count <= LARGE_OUTPUT_THRESHOLD + 20:
            self._a11y_buffer.append(data)

    def _flush_a11y_output(self) -> None:
        """Flush buffered a11y output with summary header and speedbump if needed."""
        total = self._a11y_stats.get("total", self._a11y_count)
        by_role = self._a11y_stats.get("by_role", {})

        # Print summary header
        self._print_summary_header(total, by_role)

        # Check if we hit the speedbump
        if total > LARGE_OUTPUT_THRESHOLD and not self.force:
            # Print warning
            if self.color:
                self._console.print(
                    f"[yellow]Large output: {total} elements. "
                    f"Showing preview (first 20). Use --force for full output.[/yellow]"
                )
            else:
                print(
                    f"Large output: {total} elements. "
                    f"Showing preview (first 20). Use --force for full output."
                )
            print()
            # Output only first 20 items
            for item in self._a11y_buffer[:20]:
                self._output_a11y_compact(item.get("data", item))
        else:
            # Output all buffered items
            for item in self._a11y_buffer:
                self._output_a11y_compact(item.get("data", item))

        # Reset buffer
        self._a11y_buffer = []
        self._a11y_count = 0
        self._a11y_stats = {}

    def _print_summary_header(self, total: int, by_role: dict[str, int]) -> None:
        """Print summary header showing element counts."""
        if self._summary_printed:
            return
        self._summary_printed = True

        # Build role breakdown (top 5 by count)
        sorted_roles = sorted(by_role.items(), key=lambda x: -x[1])[:5]
        role_str = ", ".join(f"{count} {role}" for role, count in sorted_roles)

        if self.color:
            self._console.print(f"[dim]# Snapshot: {total} elements ({role_str})[/dim]")
            self._console.print(
                "[dim]# Use --format full for details, --grep to filter, --force to show all[/dim]"
            )
        else:
            print(f"# Snapshot: {total} elements ({role_str})")
            print("# Use --format full for details, --grep to filter, --force to show all")
        print()

    def _output_compact(self, data: dict[str, Any]) -> None:
        """Output in compact format."""
        msg_type = data.get("type")

        if msg_type == "item":
            view = data.get("view", "")
            if view == "a11y":
                self._output_a11y_compact(data.get("data", data))
            else:
                # For non-a11y views, fall back to auto
                self._output_auto(data)
        elif msg_type == "error":
            self._output_error(data)
        elif msg_type == "done":
            # For non-a11y commands, output done normally
            # (a11y done is handled in flush above)
            self._output_done(data)
        else:
            self._output_auto(data)

    def _output_a11y_compact(self, data: dict[str, Any]) -> None:
        """Output an a11y item in compact one-line format."""
        node_id = data.get("id", "")
        role = data.get("role", "")
        name = data.get("name", "")

        # Build state indicators
        states = []
        if not data.get("enabled", True):
            states.append("disabled")
        if data.get("checked") == "true" or data.get("checked") is True:
            states.append("checked")
        if data.get("expanded") == "true" or data.get("expanded") is True:
            states.append("expanded")
        if data.get("level"):
            states.append(f"level={data.get('level')}")
        if data.get("required"):
            states.append("required")

        state_str = f" [{', '.join(states)}]" if states else ""
        name_str = f' "{name}"' if name else ""

        # Add query if present
        query = data.get("query", "")
        query_str = f"  [{query}]" if query else ""

        if self.color:
            if query:
                self._console.print(
                    f"[dim]{node_id}[/dim] {role}{name_str}{state_str}  [cyan][{query}][/cyan]"
                )
            else:
                self._console.print(f"[dim]{node_id}[/dim] {role}{name_str}{state_str}")
        else:
            print(f"{node_id} {role}{name_str}{state_str}{query_str}")

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
        # Check for brief mode first
        if data.get("brief"):
            self._output_status_brief(data)
            return

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
                self._console.print(f"  {icon} [cyan]{page_id}[/cyan] {url}")
        else:
            print(f"Session: {session_id} ({mode})")
            print(f"Pages: {len(pages)}")
            for page in pages:
                active = "*" if page.get("active") else " "
                print(f"  {active} {page.get('page_id')} {page.get('url', '')[:60]}")

    def _output_status_brief(self, data: dict[str, Any]) -> None:
        """Output one-line status summary."""
        url = data.get("url", "")[:50]
        element_count = data.get("element_count", 0)
        console = data.get("console", {})
        error_count = console.get("error", 0)
        state = data.get("state", "idle")

        if self.color:
            self._console.print(
                f"[cyan]{url}[/cyan] | {element_count} elements | "
                f"[red]{error_count}[/red] errors | {state}"
            )
        else:
            print(f"{url} | {element_count} elements | {error_count} errors | {state}")

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
                # Check if this is a11y count stats (has total and by_role)
                if "total" in summary and "by_role" in summary:
                    self._output_a11y_stats(summary)
                else:
                    if self.color:
                        self._console.print(f"[green]OK[/green] {json.dumps(summary)}")
                    else:
                        print(f"OK {json.dumps(summary)}")
        else:
            if self.color:
                error_console.print("[red]FAILED[/red]")
            else:
                print("FAILED", file=sys.stderr)

    def _output_a11y_stats(self, summary: dict[str, Any]) -> None:
        """Output a11y statistics in a readable format."""
        total = summary.get("total", 0)
        by_role = summary.get("by_role", {})

        # Sort roles by count (descending) and take top entries
        sorted_roles = sorted(by_role.items(), key=lambda x: -x[1])
        role_parts = [f"{count} {role}" for role, count in sorted_roles[:6]]
        if len(sorted_roles) > 6:
            other_count = sum(count for _, count in sorted_roles[6:])
            role_parts.append(f"{other_count} other")

        role_str = ", ".join(role_parts)

        if self.color:
            self._console.print(f"[green]{total}[/green] elements ({role_str})")
        else:
            print(f"{total} elements ({role_str})")


def print_error(message: str) -> None:
    """Print an error message."""
    error_console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]{message}[/green]")


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"[blue]{message}[/blue]")
