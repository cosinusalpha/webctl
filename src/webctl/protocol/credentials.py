"""
Peer credential verification for Unix sockets.

Verifies that connecting clients run as the same user as the daemon.
Supports Linux (SO_PEERCRED), macOS (LOCAL_PEERCRED), and Windows
(SIO_AF_UNIX_GETPEERPID + process token inspection).
"""

from __future__ import annotations

import socket
import struct
import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class PeerCredentials:
    """Credentials of the peer process."""

    uid: int
    gid: int
    pid: int | None = None
    sid: Any = None  # Windows SID (ctypes pointer)


def get_peer_credentials(sock: socket.socket) -> PeerCredentials | None:
    """
    Get credentials of the peer connected to this Unix socket.

    Returns None if platform unsupported or credentials unavailable.
    """
    if sys.platform == "linux":
        return _get_peer_credentials_linux(sock)
    elif sys.platform == "darwin":
        return _get_peer_credentials_macos(sock)
    elif sys.platform == "win32":
        return _get_peer_credentials_windows(sock)
    else:
        return None


def _get_peer_credentials_linux(sock: socket.socket) -> PeerCredentials | None:
    """Linux: Use SO_PEERCRED."""
    so_peercred = 17  # SO_PEERCRED from socket.h

    try:
        # struct ucred { pid_t pid; uid_t uid; gid_t gid; }
        cred = sock.getsockopt(socket.SOL_SOCKET, so_peercred, 12)
        pid, uid, gid = struct.unpack("iii", cred)
        return PeerCredentials(uid=uid, gid=gid, pid=pid)
    except OSError:
        return None


def _get_peer_credentials_macos(sock: socket.socket) -> PeerCredentials | None:
    """macOS: Use LOCAL_PEERCRED."""
    local_peercred = 0x001  # LOCAL_PEERCRED from sys/un.h
    sol_local = 0  # SOL_LOCAL

    try:
        # struct xucred { u_int cr_version; uid_t cr_uid; short cr_ngroups; gid_t cr_groups[16]; }
        cred = sock.getsockopt(sol_local, local_peercred, 76)
        if len(cred) < 8:
            return None
        _version, uid = struct.unpack("Ii", cred[:8])
        gid = struct.unpack("i", cred[12:16])[0] if len(cred) >= 16 else -1
        return PeerCredentials(uid=uid, gid=gid, pid=None)
    except OSError:
        return None


def _get_peer_credentials_windows(sock: socket.socket) -> PeerCredentials | None:
    """Windows: Get peer PID via ioctl, then query process token for user SID."""
    import ctypes
    from ctypes import wintypes

    # Constants from Windows headers
    sio_af_unix_getpeerpid = 0x58000100  # SIO_AF_UNIX_GETPEERPID
    process_query_limited_information = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    token_query = 0x0008  # TOKEN_QUERY

    ws2_32 = ctypes.windll.ws2_32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]

    # Set up proper function signatures for correct return value handling
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    # 1. Get peer PID from Unix socket
    peer_pid = wintypes.DWORD()
    bytes_returned = wintypes.DWORD()

    result = ws2_32.WSAIoctl(
        sock.fileno(),
        sio_af_unix_getpeerpid,
        None,
        0,
        ctypes.byref(peer_pid),
        ctypes.sizeof(peer_pid),
        ctypes.byref(bytes_returned),
        None,
        None,
    )
    if result != 0:
        return None

    # 2. Open peer process
    proc_handle = kernel32.OpenProcess(process_query_limited_information, False, peer_pid.value)
    if not proc_handle:
        return None

    try:
        # 3. Open process token
        token_handle = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(proc_handle, token_query, ctypes.byref(token_handle)):
            return None

        try:
            # 4. Get token user SID
            peer_sid = _get_token_user_sid(token_handle)
            if peer_sid is None:
                return None

            return PeerCredentials(
                uid=peer_pid.value,
                gid=0,
                pid=peer_pid.value,
                sid=peer_sid,
            )
        finally:
            kernel32.CloseHandle(token_handle)
    finally:
        kernel32.CloseHandle(proc_handle)


def _get_token_user_sid(token_handle: Any) -> Any:
    """Extract user SID from a token handle. Windows only."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    token_user = 1  # TokenUser enum value

    # First call to get buffer size
    token_info_len = wintypes.DWORD()
    advapi32.GetTokenInformation(token_handle, token_user, None, 0, ctypes.byref(token_info_len))

    # Allocate buffer and get actual info
    token_info = ctypes.create_string_buffer(token_info_len.value)
    if not advapi32.GetTokenInformation(
        token_handle,
        token_user,
        token_info,
        token_info_len,
        ctypes.byref(token_info_len),
    ):
        return None

    # TOKEN_USER structure: first field is SID_AND_ATTRIBUTES with SID pointer
    sid_ptr = ctypes.cast(token_info, ctypes.POINTER(ctypes.c_void_p)).contents
    return sid_ptr


def _get_current_user_sid() -> Any:
    """Get the SID of the current process user. Windows only."""
    import ctypes
    from ctypes import wintypes

    token_query = 0x0008  # TOKEN_QUERY
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]

    # Set up proper function signatures for correct return value handling
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    # Open current process token
    token_handle = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token_handle)
    ):
        return None

    try:
        return _get_token_user_sid(token_handle)
    finally:
        kernel32.CloseHandle(token_handle)


def verify_same_user(sock: socket.socket) -> bool:
    """
    Verify that the peer is running as the same user as this process.

    Returns False if credentials don't match or can't be determined.
    """
    creds = get_peer_credentials(sock)
    if creds is None:
        return False

    if sys.platform == "win32":
        import ctypes

        current_sid = _get_current_user_sid()
        if current_sid is None or creds.sid is None:
            return False
        advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
        return bool(advapi32.EqualSid(creds.sid, current_sid))
    else:
        import os

        return creds.uid == os.getuid()
