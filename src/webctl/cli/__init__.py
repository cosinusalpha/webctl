"""CLI for webctl."""

from .app import app
from .output import OutputFormatter, print_error, print_info, print_success

__all__ = [
    "app",
    "OutputFormatter",
    "print_error",
    "print_success",
    "print_info",
]
