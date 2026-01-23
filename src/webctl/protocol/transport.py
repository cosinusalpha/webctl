"""
Transport layer using Unix Domain Sockets.

Supports Linux, macOS, and Windows (build 17063+).
"""

import asyncio
import os
import socket
import sys
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path

# Environment variable to override socket directory
SOCKET_DIR_ENV = "WEBCTL_SOCKET_DIR"
MAX_SOCKET_PATH_LENGTH = 104  # Conservative limit for Unix sockets


class SocketError(Exception):
    """Socket error with actionable guidance."""

    pass


class Transport(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_line(self, data: str) -> None: ...

    @abstractmethod
    async def recv_line(self) -> str: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...


class TransportServer(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def get_address(self) -> str: ...


class ClientConnection(ABC):
    """Represents a single client connection on the server side."""

    @abstractmethod
    async def send_line(self, data: str) -> None: ...

    @abstractmethod
    async def recv_line(self) -> str | None: ...

    @abstractmethod
    async def close(self) -> None: ...


# === Stream-based Connection ===


ClientHandler = Callable[["ClientConnection"], Awaitable[None]]


class StreamClientConnection(ClientConnection):
    """Client connection using asyncio streams."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._verified = False

    def verify_credentials(self) -> bool:
        """Verify peer is same user. Call before processing commands."""
        sock = self._writer.get_extra_info("socket")
        if sock is None:
            return False

        from .credentials import verify_same_user

        self._verified = verify_same_user(sock)
        return self._verified

    @property
    def is_verified(self) -> bool:
        """Whether credentials have been verified."""
        return self._verified

    async def send_line(self, data: str) -> None:
        self._writer.write((data + "\n").encode())
        await self._writer.drain()

    async def recv_line(self) -> str | None:
        try:
            line = await self._reader.readline()
            if not line:
                return None
            return line.decode().rstrip("\n")
        except Exception:
            return None

    async def close(self) -> None:
        self._writer.close()
        await self._writer.wait_closed()


# === Socket Path Resolution ===


def get_socket_path(session_id: str) -> Path:
    """
    Get socket path with priority:
    1. WEBCTL_SOCKET_DIR env var (directory, session_id appended)
    2. OS-specific default
    """
    # 1. ENV override (directory, session_id still appended)
    env_dir = os.getenv(SOCKET_DIR_ENV)
    if env_dir:
        path = Path(env_dir) / f"webctl-{session_id}.sock"
    # 2. Windows: %TEMP%
    elif sys.platform == "win32":
        temp = os.environ.get("TEMP", os.environ.get("TMP", "C:\\Windows\\Temp"))
        path = Path(temp) / f"webctl-{session_id}.sock"
    # 3. Linux/macOS: /run/user/<uid>/ or /tmp/
    else:
        uid = os.getuid()
        runtime_dir = Path(f"/run/user/{uid}")
        if runtime_dir.exists():
            path = runtime_dir / f"webctl-{session_id}.sock"
        else:
            path = Path("/tmp") / f"webctl-{session_id}.sock"

    # Validate path length
    if len(str(path)) > MAX_SOCKET_PATH_LENGTH:
        raise SocketError(
            f"Socket path too long ({len(str(path))} > {MAX_SOCKET_PATH_LENGTH} chars): {path}\n"
            f"Set {SOCKET_DIR_ENV} to a shorter path."
        )

    return path


# === Unix Socket Transport ===


class UnixSocketServerTransport(TransportServer):
    """Unix domain socket server (daemon side)."""

    def __init__(self, session_id: str, client_handler: ClientHandler) -> None:
        self.socket_path = get_socket_path(session_id)
        self._server: asyncio.Server | None = None
        self._client_handler = client_handler

    async def start(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.setblocking(False)
            sock.bind(str(self.socket_path))
            sock.listen()
            self._server = await asyncio.start_server(self._handle_client, sock=sock)
        except OSError as e:
            raise SocketError(self._format_error(e)) from e

        if sys.platform != "win32":
            os.chmod(self.socket_path, 0o600)

    def _format_error(self, e: OSError) -> str:
        if sys.platform == "win32":
            return (
                f"Cannot create socket: {self.socket_path}\n"
                f"Error: {e}\n\n"
                "Possible causes:\n"
                "  - Windows version too old (requires build 17063+)\n"
                "  - Antivirus blocking socket file\n"
                "  - Path too long (max 104 chars)"
            )
        return f"Cannot create socket: {self.socket_path}\nError: {e}"

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connection = StreamClientConnection(reader, writer)

        # Verify peer credentials before processing any commands
        if not connection.verify_credentials():
            import logging

            logging.warning("Rejected connection: user credential mismatch")
            await connection.close()
            return

        await self._client_handler(connection)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self.socket_path.exists():
            self.socket_path.unlink()

    def get_address(self) -> str:
        return str(self.socket_path)


class UnixSocketClientTransport(Transport):
    """Unix domain socket client (CLI side)."""

    def __init__(self, session_id: str):
        self.socket_path = get_socket_path(session_id)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.setblocking(False)
            await asyncio.get_event_loop().sock_connect(sock, str(self.socket_path))
            self._reader, self._writer = await asyncio.open_connection(sock=sock)
        except OSError as e:
            raise SocketError(self._format_error(e)) from e

    def _format_error(self, e: OSError) -> str:
        if sys.platform == "win32":
            return (
                f"Cannot connect to socket: {self.socket_path}\n"
                f"Error: {e}\n\n"
                "Possible causes:\n"
                "  - Daemon not running (start with: webctl start)\n"
                "  - Windows version too old (requires build 17063+)\n"
                "  - Antivirus blocking socket file"
            )
        return f"Cannot connect to socket: {self.socket_path}\nError: {e}"

    async def send_line(self, data: str) -> None:
        if self._writer:
            self._writer.write((data + "\n").encode())
            await self._writer.drain()

    async def recv_line(self) -> str:
        if self._reader:
            line = await self._reader.readline()
            return line.decode().rstrip("\n")
        return ""

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()


# === Transport Factory ===


def get_server_transport(
    session_id: str,
    client_handler: ClientHandler,
) -> TransportServer:
    """Get Unix socket server transport."""
    return UnixSocketServerTransport(session_id, client_handler)


def get_client_transport(session_id: str) -> Transport:
    """Get Unix socket client transport."""
    return UnixSocketClientTransport(session_id)
