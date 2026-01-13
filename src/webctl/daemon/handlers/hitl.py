"""
HITL (Human-In-The-Loop) command handlers.
"""

from collections.abc import AsyncIterator
from typing import Any

from ...protocol.messages import DoneResponse, ErrorResponse, Request, Response
from ..session_manager import SessionManager
from .registry import register


@register("prompt-secret")
async def handle_prompt_secret(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """
    Wait for user to enter a secret (e.g., MFA code).

    This is a blocking operation that waits for the user to complete
    an action in the browser UI.
    """
    session_id = request.args.get("session", "default")
    prompt = request.args.get("prompt", "Please enter the secret:")
    request.args.get("timeout", 300000)  # 5 minutes default

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    if session.mode != "attended":
        yield ErrorResponse(
            req_id=request.req_id,
            error="prompt-secret requires attended mode",
            code="unattended_mode",
        )
        return

    # In attended mode, just acknowledge and let the user interact
    # The daemon will emit auth events and the CLI can display the prompt
    yield DoneResponse(
        req_id=request.req_id,
        ok=True,
        summary={
            "prompt": prompt,
            "waiting": True,
            "message": "User interaction required in browser window",
        },
    )


@register("ui.attach")
async def handle_ui_attach(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """
    Attach the UI (make browser visible).

    Switches from headless to headed mode if possible.
    """
    session_id = request.args.get("session", "default")

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    # Note: Playwright doesn't support switching headless modes at runtime
    # This would require browser restart. For now, we just track the intent.
    if session.mode == "unattended":
        yield ErrorResponse(
            req_id=request.req_id,
            error="Cannot attach UI to unattended session. Session must be restarted in attended mode.",
            code="mode_change_required",
        )
        return

    yield DoneResponse(
        req_id=request.req_id,
        ok=True,
        summary={"mode": session.mode, "message": "UI is attached"},
    )


@register("ui.detach")
async def handle_ui_detach(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """
    Detach the UI (hide browser window).

    Note: This doesn't actually hide the browser in Playwright,
    but indicates intent to run in background.
    """
    session_id = request.args.get("session", "default")

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    yield DoneResponse(
        req_id=request.req_id,
        ok=True,
        summary={"message": "UI detach acknowledged (browser remains visible)"},
    )


@register("wait.user")
async def handle_wait_user(
    request: Request, session_manager: SessionManager, **kwargs: Any
) -> AsyncIterator[Response]:
    """
    Wait for user to signal completion of manual action.

    Useful for CAPTCHA solving, complex form filling, etc.
    """
    session_id = request.args.get("session", "default")
    message = request.args.get("message", "Press Enter when done...")
    timeout_ms = request.args.get("timeout", 600000)  # 10 minutes default

    session = session_manager.get_session(session_id)
    if not session:
        yield ErrorResponse(
            req_id=request.req_id,
            error=f"Session '{session_id}' not found",
            code="session_not_found",
        )
        return

    if session.mode != "attended":
        yield ErrorResponse(
            req_id=request.req_id,
            error="wait.user requires attended mode",
            code="unattended_mode",
        )
        return

    # Signal to CLI that user interaction is needed
    yield DoneResponse(
        req_id=request.req_id,
        ok=True,
        summary={
            "message": message,
            "waiting": True,
            "timeout_ms": timeout_ms,
        },
    )
