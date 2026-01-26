"""Tests for peer credential verification."""

import os
import socket
import sys
import tempfile

import pytest

from webctl.protocol.credentials import (
    PeerCredentials,
    get_peer_credentials,
    verify_same_user,
)


def _create_unix_socket_pair_windows():
    """Create a connected Unix socket pair on Windows using ctypes."""
    # Import to get the monkey-patched AF_UNIX
    from webctl.protocol.transport import _win_accept_unix, _win_bind_unix, _win_connect_unix

    sock_path = os.path.join(tempfile.gettempdir(), f"test-creds-{os.getpid()}.sock")
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    _win_bind_unix(server, sock_path)
    server.listen(1)

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    _win_connect_unix(client, sock_path)

    conn_fd = _win_accept_unix(server)
    conn = socket.socket(fileno=conn_fd)

    # Clean up the socket file
    os.unlink(sock_path)

    # Return conn (server-side accepted socket) and client
    # Close the listening server socket
    server.close()

    return conn, client


class TestCredentials:
    """Test credential extraction on each platform."""

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix only")
    def test_get_peer_credentials_returns_current_uid(self):
        """Socket pair should have same UID as current process."""
        server, client = socket.socketpair(socket.AF_UNIX)
        try:
            creds = get_peer_credentials(client)
            assert creds is not None
            assert isinstance(creds, PeerCredentials)
            assert creds.uid == os.getuid()
            assert creds.gid == os.getgid()
        finally:
            server.close()
            client.close()

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix only")
    def test_verify_same_user_passes_for_self(self):
        """Same user connection should pass verification."""
        server, client = socket.socketpair(socket.AF_UNIX)
        try:
            assert verify_same_user(client) is True
        finally:
            server.close()
            client.close()

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
    def test_linux_returns_pid(self):
        """Linux SO_PEERCRED should return PID."""
        server, client = socket.socketpair(socket.AF_UNIX)
        try:
            creds = get_peer_credentials(client)
            assert creds is not None
            assert creds.pid is not None
            assert creds.pid > 0
        finally:
            server.close()
            client.close()

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
    def test_macos_credentials(self):
        """macOS LOCAL_PEERCRED should return UID/GID."""
        server, client = socket.socketpair(socket.AF_UNIX)
        try:
            creds = get_peer_credentials(client)
            assert creds is not None
            assert creds.uid == os.getuid()
            # macOS doesn't return PID via LOCAL_PEERCRED
            assert creds.pid is None
        finally:
            server.close()
            client.close()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_get_peer_credentials_windows(self):
        """Windows: socket pair should return credentials with PID."""
        server, client = _create_unix_socket_pair_windows()
        try:
            creds = get_peer_credentials(server)
            assert creds is not None
            assert creds.pid is not None
            assert creds.pid > 0
            assert creds.pid == os.getpid()
        finally:
            server.close()
            client.close()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_verify_same_user_windows(self):
        """Windows: same user connection should pass."""
        server, client = _create_unix_socket_pair_windows()
        try:
            assert verify_same_user(server) is True
        finally:
            server.close()
            client.close()

    def test_get_peer_credentials_invalid_socket(self):
        """Invalid socket should return None, not crash."""
        # Create a TCP socket (not Unix) - credentials won't work
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # This should return None or handle gracefully
            creds = get_peer_credentials(sock)
            # On most platforms, this should return None for non-Unix sockets
            # The important thing is it doesn't crash
            assert creds is None or isinstance(creds, PeerCredentials)
        finally:
            sock.close()
