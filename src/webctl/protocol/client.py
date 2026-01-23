"""
IPC client for CLI-to-daemon communication.
"""

import json
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

from .messages import (
    DoneResponse,
    ErrorResponse,
    EventResponse,
    ItemResponse,
    Request,
    Response,
)
from .transport import get_client_transport


class DaemonClient:
    """Client for communicating with the webctl daemon."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.transport = get_client_transport(session_id)

    async def connect(self) -> None:
        """Connect to the daemon."""
        await self.transport.connect()

    async def send_command(
        self, command: str, args: dict[str, Any] | None = None
    ) -> AsyncIterator[Response]:
        """Send command and stream responses."""
        request = Request(command=command, args=args or {})
        await self.transport.send_line(request.model_dump_json())

        while True:
            line = await self.transport.recv_line()
            if not line:
                break

            data = json.loads(line)

            if data["type"] == "done":
                yield DoneResponse(**data)
                break
            elif data["type"] == "item":
                yield ItemResponse(**data)
            elif data["type"] == "event":
                yield EventResponse(**data)
            elif data["type"] == "error":
                yield ErrorResponse(**data)
                break

    async def close(self) -> None:
        """Close the connection."""
        await self.transport.close()

    async def __aenter__(self) -> "DaemonClient":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
