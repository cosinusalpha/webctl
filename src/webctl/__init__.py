"""
webctl - Stateful, agent-first browser interface.

A CLI tool for automating browser interactions with accessibility-first design.
"""

__version__ = "0.1.0"


def main() -> None:
    """Main entry point for webctl CLI."""
    from .cli.app import app

    app()
