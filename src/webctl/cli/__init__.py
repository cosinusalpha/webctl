"""CLI for webctl."""

from .app import app
from .output import OutputFormatter, print_error, print_info, print_success, print_warning

__all__ = [
    "app",
    "OutputFormatter",
    "print_error",
    "print_success",
    "print_warning",
    "print_info",
]
