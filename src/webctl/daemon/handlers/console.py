"""
Console log command handler.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any

from ...protocol.messages import DoneResponse, ErrorResponse, ItemResponse, Request, Response
from ..session_manager import SessionManager
from .registry import register


@register("console")
async def handle_console(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Get or stream browser console logs."""
    session_id = request.args.get("session", "default")
    follow = request.args.get("follow", False)
    level = request.args.get("level")  # None = all levels
    limit = request.args.get("limit", 100)
    count_only = request.args.get("count_only", False)

    page_info = session_manager.get_active_page_info(session_id)
    if not page_info:
        yield ErrorResponse(
            req_id=request.req_id,
            error="No active page",
            code="no_active_page",
        )
        return

    try:
        # Count only mode - return counts by level
        if count_only:
            counts: Counter[str] = Counter()
            for log in page_info.console_logs:
                counts[log["level"]] += 1

            yield ItemResponse(
                req_id=request.req_id,
                view="console_counts",
                data={
                    "total": len(page_info.console_logs),
                    "by_level": dict(counts),
                },
            )
            yield DoneResponse(req_id=request.req_id, ok=True)
            return

        # Get buffered logs
        logs = page_info.console_logs[-limit:] if limit else page_info.console_logs[:]
        if level:
            logs = [log for log in logs if log["level"] == level]

        initial_count = len(logs)

        for log in logs:
            yield ItemResponse(
                req_id=request.req_id,
                view="console",
                data=log,
            )

        if follow:
            # Stream new logs
            last_index = len(page_info.console_logs)
            while True:
                await asyncio.sleep(0.5)
                current_logs = page_info.console_logs
                if len(current_logs) > last_index:
                    for log in current_logs[last_index:]:
                        if not level or log["level"] == level:
                            yield ItemResponse(
                                req_id=request.req_id,
                                view="console",
                                data=log,
                            )
                    last_index = len(current_logs)
        else:
            yield DoneResponse(
                req_id=request.req_id,
                ok=True,
                summary={"count": initial_count},
            )

    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))
