"""
Daemon server for webctl.

The daemon manages browser sessions and handles commands from CLI clients.
"""

import asyncio
import contextlib
import json
import signal
import sys

from ..config import DEFAULT_IDLE_TIMEOUT, WebctlConfig
from ..protocol.messages import ErrorResponse, EventResponse, Request
from ..protocol.transport import (
    ClientConnection,
    TransportServer,
    get_server_transport,
)
from .event_emitter import EventEmitter
from .handlers import get_handler
from .session_manager import SessionManager


class DaemonServer:
    """Main daemon server."""

    def __init__(
        self,
        session_id: str = "default",
        config: WebctlConfig | None = None,
    ):
        self.session_id = session_id
        self.config = config or WebctlConfig.load()

        self._event_emitter = EventEmitter()
        self._session_manager = SessionManager(self._event_emitter)
        self._transport: TransportServer | None = None
        self._running = False
        self._idle_timeout = self.config.idle_timeout or DEFAULT_IDLE_TIMEOUT
        self._last_activity: float = 0.0  # Will be set when event loop starts
        self._client_count = 0

    async def start(self) -> None:
        """Start the daemon server."""
        self._running = True
        self._last_activity = asyncio.get_running_loop().time()

        await self._event_emitter.start()

        self._transport = get_server_transport(self.session_id, self._handle_client)
        await self._transport.start()

        print(f"webctl daemon started on {self._transport.get_address()}")

        # Start idle monitor
        asyncio.create_task(self._idle_monitor())

        # Keep server running
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop the daemon server."""
        self._running = False

        # Close all sessions
        await self._session_manager.close_all()

        # Stop event emitter
        await self._event_emitter.stop()

        # Close transport
        if self._transport:
            await self._transport.close()

        print("webctl daemon stopped")

    async def _handle_client(self, connection: ClientConnection) -> None:
        """Handle a client connection."""
        self._client_count += 1
        self._last_activity = asyncio.get_running_loop().time()

        # Subscribe to events
        async def send_event(event: EventResponse) -> None:
            with contextlib.suppress(Exception):
                await connection.send_line(event.model_dump_json())

        self._event_emitter.subscribe(send_event)

        try:
            while self._running:
                line = await connection.recv_line()
                if line is None:
                    break

                self._last_activity = asyncio.get_running_loop().time()

                try:
                    # Parse request
                    data = json.loads(line)
                    request = Request(**data)

                    # Get handler
                    handler = get_handler(request.command)
                    if not handler:
                        error = ErrorResponse(
                            req_id=request.req_id,
                            error=f"Unknown command: {request.command}",
                            code="unknown_command",
                        )
                        await connection.send_line(error.model_dump_json())
                        continue

                    # Execute handler
                    async for response in handler(
                        request,
                        session_manager=self._session_manager,
                        event_emitter=self._event_emitter,
                        server=self,
                    ):
                        await connection.send_line(response.model_dump_json())

                except json.JSONDecodeError as e:
                    error = ErrorResponse(error=f"Invalid JSON: {e}")
                    await connection.send_line(error.model_dump_json())
                except Exception as e:
                    error = ErrorResponse(error=str(e))
                    await connection.send_line(error.model_dump_json())

        finally:
            self._event_emitter.unsubscribe(send_event)
            self._client_count -= 1
            await connection.close()

    async def _idle_monitor(self) -> None:
        """Monitor for idle timeout."""
        while self._running:
            await asyncio.sleep(60)  # Check every minute

            if self._client_count == 0:
                idle_time = asyncio.get_running_loop().time() - self._last_activity
                if idle_time > self._idle_timeout:
                    print(f"Idle timeout ({self._idle_timeout}s) reached, shutting down")
                    await self.stop()
                    break


def main() -> None:
    """Main entry point for the daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="webctl daemon")
    parser.add_argument("--session", default="default", help="Session ID")
    parser.add_argument("--config", help="Config file path")
    args = parser.parse_args()

    config = None
    if args.config:
        from pathlib import Path

        config = WebctlConfig.load(Path(args.config))

    server = DaemonServer(session_id=args.session, config=config)

    # Handle signals
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown_handler() -> None:
        loop.create_task(server.stop())

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, shutdown_handler)
        loop.add_signal_handler(signal.SIGINT, shutdown_handler)

    try:
        loop.run_until_complete(server.start())
    except KeyboardInterrupt:
        loop.run_until_complete(server.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
