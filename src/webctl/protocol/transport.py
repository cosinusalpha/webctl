"""
Transport layer supporting:
- Named Pipes (Windows) - primary
- Unix Domain Sockets (Linux/macOS) - primary
- TCP loopback (fallback) - RFC SS5.1
"""

import asyncio
import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class TransportType(str, Enum):
    NAMED_PIPE = "named_pipe"
    UNIX_SOCKET = "unix_socket"
    TCP = "tcp"


@dataclass
class TransportConfig:
    """Transport configuration."""

    type: TransportType
    session_id: str
    tcp_host: str = "127.0.0.1"
    tcp_port: int | None = None  # Auto-assign if None


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


class StreamClientConnection(ClientConnection):
    """Client connection using asyncio streams."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer

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


# === Named Pipe Transport (Windows) ===


ClientHandler = Callable[["ClientConnection"], Awaitable[None]]


class NamedPipeServerTransport(TransportServer):
    """Windows named pipe server (daemon side)."""

    def __init__(self, session_id: str, client_handler: ClientHandler) -> None:
        self.pipe_path = f"\\\\.\\pipe\\webctl-{session_id}"
        self._client_handler = client_handler
        self._running = False

    async def start(self) -> None:
        """Start the named pipe server."""
        self._running = True
        # On Windows, we use a TCP fallback since asyncio named pipes
        # require special handling. For full Windows support, consider
        # using win32pipe directly or a library like aiowpipe.
        # For now, we'll use TCP as the cross-platform solution.
        pass

    async def close(self) -> None:
        self._running = False

    def get_address(self) -> str:
        return self.pipe_path


class NamedPipeClientTransport(Transport):
    """Windows named pipe client (CLI side)."""

    def __init__(self, session_id: str):
        self.pipe_path = f"\\\\.\\pipe\\webctl-{session_id}"
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        # For Windows, we'll use TCP fallback
        pass

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


# === Unix Socket Transport (non-Windows only) ===

if sys.platform != "win32":

    class UnixSocketServerTransport(TransportServer):  # noqa: E301
        """Unix domain socket server (daemon side)."""

        def __init__(self, session_id: str, client_handler: ClientHandler) -> None:
            runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
            self.socket_path = runtime_dir / f"webctl-{session_id}.sock"
            self._server: asyncio.Server | None = None
            self._client_handler = client_handler

        async def start(self) -> None:
            # Remove stale socket
            if self.socket_path.exists():
                self.socket_path.unlink()

            self._server = await asyncio.start_unix_server(
                self._handle_client, path=str(self.socket_path)
            )

        async def _handle_client(
            self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            connection = StreamClientConnection(reader, writer)
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
            runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
            self.socket_path = runtime_dir / f"webctl-{session_id}.sock"
            self._reader: asyncio.StreamReader | None = None
            self._writer: asyncio.StreamWriter | None = None

        async def connect(self) -> None:
            self._reader, self._writer = await asyncio.open_unix_connection(
                path=str(self.socket_path)
            )

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

else:
    # Stub classes for Windows - these exist for type checking but are never used
    # (Windows always uses TCP transport)

    class UnixSocketServerTransport(TransportServer):
        """Stub for Windows - not used at runtime."""

        def __init__(self, session_id: str, client_handler: ClientHandler) -> None:
            raise NotImplementedError("Unix sockets not available on Windows")

        async def start(self) -> None:
            raise NotImplementedError("Unix sockets not available on Windows")

        async def close(self) -> None:
            raise NotImplementedError("Unix sockets not available on Windows")

        def get_address(self) -> str:
            raise NotImplementedError("Unix sockets not available on Windows")

    class UnixSocketClientTransport(Transport):
        """Stub for Windows - not used at runtime."""

        def __init__(self, session_id: str) -> None:
            raise NotImplementedError("Unix sockets not available on Windows")

        async def connect(self) -> None:
            raise NotImplementedError("Unix sockets not available on Windows")

        async def send_line(self, data: str) -> None:
            raise NotImplementedError("Unix sockets not available on Windows")

        async def recv_line(self) -> str:
            raise NotImplementedError("Unix sockets not available on Windows")

        async def close(self) -> None:
            raise NotImplementedError("Unix sockets not available on Windows")

        def is_connected(self) -> bool:
            raise NotImplementedError("Unix sockets not available on Windows")


# === TCP Transport (Fallback - RFC SS5.1) ===


class TCPServerTransport(TransportServer):
    """TCP loopback server (fallback transport)."""

    def __init__(
        self,
        session_id: str,
        client_handler: ClientHandler,
        host: str = "127.0.0.1",
        port: int | None = None,
    ) -> None:
        self.session_id = session_id
        self.host = host
        self.port = port or self._get_default_port(session_id)
        self._server: asyncio.Server | None = None
        self._client_handler = client_handler

    def _get_default_port(self, session_id: str) -> int:
        """Generate consistent port from session_id (49152-65535 range)."""
        import hashlib

        hash_val = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16)
        return 49152 + (hash_val % 16383)  # Ephemeral port range

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, host=self.host, port=self.port
        )
        # Get actual port if auto-assigned
        if self._server.sockets:
            addr = self._server.sockets[0].getsockname()
            self.port = addr[1]

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connection = StreamClientConnection(reader, writer)
        await self._client_handler(connection)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def get_address(self) -> str:
        return f"tcp://{self.host}:{self.port}"


class TCPClientTransport(Transport):
    """TCP loopback client (fallback transport)."""

    def __init__(
        self,
        session_id: str,
        host: str = "127.0.0.1",
        port: int | None = None,
    ):
        self.session_id = session_id
        self.host = host
        self.port = port or self._get_default_port(session_id)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    def _get_default_port(self, session_id: str) -> int:
        import hashlib

        hash_val = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16)
        return 49152 + (hash_val % 16383)

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(host=self.host, port=self.port)

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


def get_socket_path(session_id: str) -> Path:
    """Get platform-appropriate socket path."""
    if sys.platform == "win32":
        return Path(f"\\\\.\\pipe\\webctl-{session_id}")
    else:
        runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        return runtime_dir / f"webctl-{session_id}.sock"


def get_server_transport(
    session_id: str,
    client_handler: ClientHandler,
    transport_type: TransportType | None = None,
    tcp_port: int | None = None,
) -> TransportServer:
    """Get appropriate server transport for platform."""
    # Windows always uses TCP
    if sys.platform == "win32":
        return TCPServerTransport(session_id, client_handler, port=tcp_port)

    # Non-Windows: use Unix socket unless TCP explicitly requested
    if transport_type == TransportType.TCP:
        return TCPServerTransport(session_id, client_handler, port=tcp_port)

    return UnixSocketServerTransport(session_id, client_handler)


def get_client_transport(
    session_id: str,
    transport_type: TransportType | None = None,
    tcp_port: int | None = None,
) -> Transport:
    """Get appropriate client transport for platform."""
    # Windows always uses TCP
    if sys.platform == "win32":
        return TCPClientTransport(session_id, port=tcp_port)

    # Non-Windows: use Unix socket unless TCP explicitly requested
    if transport_type == TransportType.TCP:
        return TCPClientTransport(session_id, port=tcp_port)

    return UnixSocketClientTransport(session_id)
