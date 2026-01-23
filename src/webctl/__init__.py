"""
webctl - Stateful, agent-first browser interface.

A CLI tool for automating browser interactions with accessibility-first design.
"""


def main() -> None:
    """Main entry point for webctl CLI."""
    from .cli.app import app

    app()
