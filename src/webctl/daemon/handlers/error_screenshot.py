"""
Screenshot capture on error for debugging.
"""

import tempfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page

from ...config import WebctlConfig


async def capture_error_screenshot(
    page: Page,
    command: str,
    error_code: str,
) -> str | None:
    """
    Capture a screenshot when an error occurs.

    Returns the path to the screenshot, or None if disabled/failed.
    """
    config = WebctlConfig.load()

    if not config.screenshot_on_error:
        return None

    try:
        # Determine output directory
        if config.screenshot_error_dir:
            output_dir = Path(config.screenshot_error_dir)
        else:
            output_dir = Path(tempfile.gettempdir()) / "webctl-errors"

        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_command = command.replace(".", "-")
        filename = f"{timestamp}_{safe_command}_{error_code}.png"
        filepath = output_dir / filename

        # Capture screenshot
        await page.screenshot(path=str(filepath))

        return str(filepath)

    except Exception:
        # Don't let screenshot capture break the error flow
        return None
