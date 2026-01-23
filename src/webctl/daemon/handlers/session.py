"""
Session command handlers.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any

from ...protocol.messages import DoneResponse, ErrorResponse, ItemResponse, Request, Response
from ..session_manager import SessionManager
from .registry import register


@register("session.start")
async def handle_session_start(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Start a new browser session."""
    session_id = request.args.get("session", "default")
    mode = request.args.get("mode", "attended")

    if session_manager.get_session(session_id):
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' already exists",
            code="session_exists",
        )
        return

    try:
        session = await session_manager.create_session(session_id, mode=mode)
        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary={
                "session_id": session.session_id,
                "mode": session.mode,
                "page_id": session.active_page_id,
            },
        )
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("session.stop")
async def handle_session_stop(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Stop a browser session."""
    session_id = request.args.get("session", "default")

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    try:
        await session_manager.close_session(session_id)
        yield DoneResponse(req_id=request.req_id, ok=True)
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=str(e))


@register("session.status")
async def handle_session_status(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Get session status."""
    session_id = request.args.get("session", "default")
    brief = request.args.get("brief", False)

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    pages = session_manager.list_pages(session_id)

    # Get console log counts for active page
    console_counts: dict[str, int] = {}
    page_info = session_manager.get_active_page_info(session_id)
    if page_info:
        counts: Counter[str] = Counter()
        for log in page_info.console_logs:
            counts[log["level"]] += 1
        console_counts = dict(counts)

    # Get current URL and page state
    url = ""
    title = ""
    state = "idle"
    page = session_manager.get_active_page(session_id)
    if page:
        url = page.url
        try:
            title = await page.title()
        except Exception:
            title = ""

    # For brief mode, get element count (lightweight estimate)
    element_count = 0
    if brief and page:
        try:
            snapshot_str = await page.locator("body").aria_snapshot()
            # Quick count: each "- " prefix indicates an element
            element_count = snapshot_str.count("\n- ") + 1 if snapshot_str else 0
        except Exception:
            element_count = 0

    yield ItemResponse(
        req_id=request.req_id,
        view="status",
        data={
            "session_id": session.session_id,
            "mode": session.mode,
            "active_page_id": session.active_page_id,
            "page_count": len(pages),
            "pages": pages,
            "console": console_counts,
            "url": url,
            "title": title,
            "state": state,
            "element_count": element_count,
            "brief": brief,
        },
    )
    yield DoneResponse(req_id=request.req_id, ok=True)


@register("session.list")
async def handle_session_list(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """List all sessions."""
    sessions = session_manager.list_sessions()

    for session_id in sessions:
        session = session_manager.get_session(session_id)
        if session:
            yield ItemResponse(
                req_id=request.req_id,
                view="session",
                data={
                    "session_id": session.session_id,
                    "mode": session.mode,
                    "page_count": len(session.pages),
                },
            )

    yield DoneResponse(req_id=request.req_id, ok=True, summary={"count": len(sessions)})


@register("page.focus")
async def handle_page_focus(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Focus a specific page."""
    session_id = request.args.get("session", "default")
    page_id = request.args.get("page_id")

    if not page_id:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'page_id' argument",
            code="missing_argument",
        )
        return

    success = session_manager.set_active_page(session_id, page_id)
    if success:
        yield DoneResponse(req_id=request.req_id, ok=True)
    else:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Page '{page_id}' not found in session '{session_id}'",
            code="page_not_found",
        )


@register("page.close")
async def handle_page_close(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Close a specific page."""
    session_id = request.args.get("session", "default")
    page_id = request.args.get("page_id")

    if not page_id:
        yield ErrorResponse(
            req_id=request.req_id,
            error="Missing 'page_id' argument",
            code="missing_argument",
        )
        return

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    page_info = session.pages.get(page_id)
    if not page_info:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Page '{page_id}' not found",
            code="page_not_found",
        )
        return

    try:
        await page_info.page.close()
        yield DoneResponse(req_id=request.req_id, ok=True, summary={"closed": page_id})
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=f"Failed to close page: {e}")


@register("session.save")
async def handle_session_save(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """Save session state to disk."""
    session_id = request.args.get("session", "default")

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    try:
        await session_manager.save_session(session_id)
        yield DoneResponse(
            req_id=request.req_id,
            ok=True,
            summary={
                "session_id": session_id,
                "profile_dir": str(session.profile_dir),
                "message": "Session state saved",
            },
        )
    except Exception as e:
        yield ErrorResponse(req_id=request.req_id, error=f"Failed to save: {e}")


@register("session.profiles")
async def handle_session_profiles(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """List available stored session profiles."""
    from ...config import get_base_profile_dir

    base_dir = get_base_profile_dir()
    profiles: list[dict[str, Any]] = []

    if base_dir.exists():
        for profile_dir in base_dir.iterdir():
            if profile_dir.is_dir():
                state_file = profile_dir / "state.json"
                has_state = state_file.exists()
                profiles.append(
                    {
                        "name": profile_dir.name,
                        "has_saved_state": has_state,
                        "path": str(profile_dir),
                    }
                )

    for profile in profiles:
        yield ItemResponse(
            req_id=request.req_id,
            view="profile",
            data=profile,
        )

    yield DoneResponse(
        req_id=request.req_id,
        ok=True,
        summary={"count": len(profiles)},
    )


@register("daemon.shutdown")
async def handle_daemon_shutdown(
    request: Request, session_manager: SessionManager, server: Any = None, **kwargs: Any
) -> AsyncIterator[Response]:
    """Shutdown the daemon server."""
    yield DoneResponse(req_id=request.req_id, ok=True, summary={"message": "Daemon shutting down"})

    # Schedule shutdown after response is sent
    if server:
        asyncio.create_task(server.stop())
