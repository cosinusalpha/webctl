"""
Transport layer using Unix Domain Sockets.

Supports Linux, macOS, and Windows (build 17063+).
"""

import asyncio
import logging
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


# === Windows AF_UNIX Support ===
# Python's socket module doesn't expose AF_UNIX on Windows, but the OS supports it.
# We use ctypes to work around this limitation.

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # Monkey-patch the missing constant
    socket.AF_UNIX = 1  # type: ignore[attr-defined]

    class _sockaddr_un(ctypes.Structure):  # noqa: N801 - matches C struct name
        """Windows sockaddr_un structure."""

        _fields_ = [("sun_family", ctypes.c_ushort), ("sun_path", ctypes.c_char * 108)]

    _ws2_32 = ctypes.windll.ws2_32

    # Set up proper function signatures
    _ws2_32.bind.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    _ws2_32.bind.restype = ctypes.c_int
    _ws2_32.connect.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    _ws2_32.connect.restype = ctypes.c_int
    _ws2_32.accept.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
    _ws2_32.accept.restype = wintypes.UINT
    _ws2_32.WSAGetLastError.argtypes = []
    _ws2_32.WSAGetLastError.restype = ctypes.c_int

    def _win_bind_unix(sock: socket.socket, path: str) -> None:
        """Bind a socket to a Unix domain socket path on Windows."""
        addr = _sockaddr_un(sun_family=1, sun_path=path.encode())
        ret = _ws2_32.bind(sock.fileno(), ctypes.byref(addr), ctypes.sizeof(addr))
        if ret != 0:
            err = _ws2_32.WSAGetLastError()
            raise OSError(f"bind failed with error {err}")

    def _win_connect_unix(sock: socket.socket, path: str) -> None:
        """Connect a socket to a Unix domain socket path on Windows."""
        addr = _sockaddr_un(sun_family=1, sun_path=path.encode())
        ret = _ws2_32.connect(sock.fileno(), ctypes.byref(addr), ctypes.sizeof(addr))
        if ret != 0:
            err = _ws2_32.WSAGetLastError()
            raise OSError(f"connect failed with error {err}")

    def _win_accept_unix(sock: socket.socket) -> int | None:
        """Accept a connection on Windows. Returns fd or None if would block."""
        fd = _ws2_32.accept(sock.fileno(), None, None)
        # Check for INVALID_SOCKET (-1 as unsigned)
        if fd == 0xFFFFFFFF or fd == 0xFFFFFFFFFFFFFFFF:
            err = _ws2_32.WSAGetLastError()
            if err == 10035:  # WSAEWOULDBLOCK
                return None
            raise OSError(f"accept failed with error {err}")
        return fd


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


# === Unix Socket Transport (Linux/macOS) ===


class UnixSocketServerTransport(TransportServer):
    """Unix domain socket server using native asyncio (Linux/macOS)."""

    def __init__(self, session_id: str, client_handler: ClientHandler) -> None:
        self.socket_path = get_socket_path(session_id)
        self._server: asyncio.Server | None = None
        self._client_handler = client_handler

    async def start(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()

        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client, path=str(self.socket_path)
            )
        except OSError as e:
            raise SocketError(f"Cannot create socket: {self.socket_path}\nError: {e}") from e

        os.chmod(self.socket_path, 0o600)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connection = StreamClientConnection(reader, writer)

        if not connection.verify_credentials():
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
    """Unix domain socket client using native asyncio (Linux/macOS)."""

    def __init__(self, session_id: str):
        self.socket_path = get_socket_path(session_id)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                path=str(self.socket_path)
            )
        except OSError as e:
            raise SocketError(f"Cannot connect to socket: {self.socket_path}\nError: {e}") from e

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


# === Windows Unix Socket Transport ===

if sys.platform == "win32":

    class WindowsUnixSocketServerTransport(TransportServer):
        """Unix domain socket server for Windows using ctypes."""

        def __init__(self, session_id: str, client_handler: ClientHandler) -> None:
            self.socket_path = get_socket_path(session_id)
            self._socket: socket.socket | None = None
            self._client_handler = client_handler
            self._running = False
            self._accept_task: asyncio.Task[None] | None = None

        async def start(self) -> None:
            if self.socket_path.exists():
                self.socket_path.unlink()

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                _win_bind_unix(sock, str(self.socket_path))
                sock.listen(5)
                sock.setblocking(False)
            except OSError as e:
                sock.close()
                raise SocketError(self._format_error(e)) from e

            self._socket = sock
            self._running = True
            self._accept_task = asyncio.create_task(self._accept_loop())

        def _format_error(self, e: OSError) -> str:
            return (
                f"Cannot create socket: {self.socket_path}\n"
                f"Error: {e}\n\n"
                "Possible causes:\n"
                "  - Windows version too old (requires build 17063+)\n"
                "  - Antivirus blocking socket file\n"
                "  - Path too long (max 104 chars)"
            )

        async def _accept_loop(self) -> None:
            while self._running and self._socket:
                try:
                    fd = _win_accept_unix(self._socket)
                    if fd is not None:
                        try:
                            conn_sock = socket.socket(fileno=fd)
                        except OSError:
                            os.close(fd)
                            raise
                        conn_sock.setblocking(False)
                        asyncio.create_task(self._handle_connection(conn_sock))
                    else:
                        await asyncio.sleep(0.01)
                except OSError as e:
                    if self._running:
                        logging.warning(f"Accept error: {e}")
                    break

        async def _handle_connection(self, sock: socket.socket) -> None:
            """Handle an accepted connection."""
            try:
                reader, writer = await asyncio.open_connection(sock=sock)
                connection = StreamClientConnection(reader, writer)

                if not connection.verify_credentials():
                    logging.warning("Rejected connection: user credential mismatch")
                    await connection.close()
                    return

                await self._client_handler(connection)
            except Exception as e:
                logging.warning(f"Connection handler error: {e}")

        async def close(self) -> None:
            self._running = False
            if self._accept_task:
                self._accept_task.cancel()
                try:
                    await self._accept_task
                except asyncio.CancelledError:
                    pass
            if self._socket:
                self._socket.close()
            if self.socket_path.exists():
                self.socket_path.unlink()

        def get_address(self) -> str:
            return str(self.socket_path)

    class WindowsUnixSocketClientTransport(Transport):
        """Unix domain socket client for Windows using ctypes."""

        def __init__(self, session_id: str):
            self.socket_path = get_socket_path(session_id)
            self._reader: asyncio.StreamReader | None = None
            self._writer: asyncio.StreamWriter | None = None

        async def connect(self) -> None:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                _win_connect_unix(sock, str(self.socket_path))
                sock.setblocking(False)
                self._reader, self._writer = await asyncio.open_connection(sock=sock)
            except OSError as e:
                raise SocketError(self._format_error(e)) from e

        def _format_error(self, e: OSError) -> str:
            return (
                f"Cannot connect to socket: {self.socket_path}\n"
                f"Error: {e}\n\n"
                "Possible causes:\n"
                "  - Daemon not running (start with: webctl start)\n"
                "  - Windows version too old (requires build 17063+)\n"
                "  - Antivirus blocking socket file"
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


# === Transport Factory ===


def get_server_transport(
    session_id: str,
    client_handler: ClientHandler,
) -> TransportServer:
    """Get platform-appropriate Unix socket server transport."""
    if sys.platform == "win32":
        return WindowsUnixSocketServerTransport(session_id, client_handler)
    return UnixSocketServerTransport(session_id, client_handler)


def get_client_transport(session_id: str) -> Transport:
    """Get platform-appropriate Unix socket client transport."""
    if sys.platform == "win32":
        return WindowsUnixSocketClientTransport(session_id)
    return UnixSocketClientTransport(session_id)
