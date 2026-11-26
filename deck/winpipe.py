"""Local named pipes between the deck's processes, with an access list, and who is on the other end.

    server = PipeServer("ulanzi-deck-agent", sddl, on_client)   # on_client(conn, Identity) on the accept thread
    server.start(); ...; server.close()
    conn = connect("ulanzi-deck-agent")       # raises OSError when nothing is listening (yet)
    sddl_for(user_sid) / SYSTEM_ONLY          # access lists for the two pipes

Connections are multiprocessing.connection.PipeConnection objects (message mode, overlapped I/O), which is what
deck.link.Peer talks over. The server is created with FILE_FLAG_FIRST_PIPE_INSTANCE, so if another program got
the name first, start() fails instead of sharing it, and it rejects remote clients. `Identity` (process id,
Windows session, user SID) is read from the client's process token, never taken from what it says.
"""
from __future__ import annotations

import _winapi
import ctypes
import logging
import threading
from ctypes import wintypes
from dataclasses import dataclass
from multiprocessing.connection import Client, PipeConnection
from typing import Callable

log = logging.getLogger(__name__)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PIPE_ACCESS_DUPLEX = 0x3
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_MESSAGE, PIPE_READMODE_MESSAGE, PIPE_REJECT_REMOTE_CLIENTS = 0x4, 0x2, 0x8
PIPE_UNLIMITED_INSTANCES = 255
INVALID_HANDLE = ctypes.c_void_p(-1).value
WAIT_TIMEOUT = 0x102
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x8
TOKEN_USER = 1

SYSTEM_ONLY = "D:P(A;;GA;;;SY)"


def sddl_for(user_sid: str) -> str:
    """SYSTEM and Administrators: full; this one user: read/write; network logons: denied."""
    return f"D:P(D;;GA;;;NU)(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;{user_sid})"


@dataclass(frozen=True)
class Identity:
    pid: int
    session: int
    sid: str


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.POINTER(_SECURITY_ATTRIBUTES)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
kernel32.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]


def _path(name: str) -> str:
    return rf"\\.\pipe\{name}"


def _check(ok, what: str):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)
    return ok


def process_sid(pid: int) -> str:
    """The user SID a process runs as."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    _check(h, f"OpenProcess({pid})")
    try:
        tok = wintypes.HANDLE()
        _check(advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)), "OpenProcessToken")
        try:
            size = wintypes.DWORD()
            advapi32.GetTokenInformation(tok, TOKEN_USER, None, 0, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            _check(advapi32.GetTokenInformation(tok, TOKEN_USER, buf, size, ctypes.byref(size)),
                   "GetTokenInformation")
            psid = ctypes.c_void_p.from_buffer(buf).value      # TOKEN_USER.User.Sid is the first field
            text = ctypes.c_wchar_p()
            _check(advapi32.ConvertSidToStringSidW(psid, ctypes.byref(text)), "ConvertSidToStringSid")
            try:
                return text.value
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def process_session(pid: int) -> int:
    s = wintypes.DWORD()
    _check(kernel32.ProcessIdToSessionId(pid, ctypes.byref(s)), "ProcessIdToSessionId")
    return s.value


def identity_of(pid: int) -> Identity:
    return Identity(pid, process_session(pid), process_sid(pid))


def client_identity(conn: PipeConnection) -> Identity:
    pid = wintypes.ULONG()
    _check(kernel32.GetNamedPipeClientProcessId(conn.fileno(), ctypes.byref(pid)), "GetNamedPipeClientProcessId")
    return identity_of(pid.value)


def server_identity(conn: PipeConnection) -> Identity:
    pid = wintypes.ULONG()
    _check(kernel32.GetNamedPipeServerProcessId(conn.fileno(), ctypes.byref(pid)), "GetNamedPipeServerProcessId")
    return identity_of(pid.value)


def connect(name: str) -> PipeConnection:
    """Connect to a deck pipe; OSError when no server is listening."""
    return Client(_path(name), family="AF_PIPE")


class PipeServer:
    def __init__(self, name: str, sddl: str, on_client: Callable[[PipeConnection, Identity], None]):
        self.name = name
        self._on_client = on_client
        self._stop = threading.Event()
        self._sd = ctypes.c_void_p()
        _check(advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(self._sd), None),
               "ConvertStringSecurityDescriptorToSecurityDescriptor")
        self._sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), self._sd, False)
        self._first = True
        self._handle = None

    def start(self) -> "PipeServer":
        self._handle = self._create()    # the first instance now, so a name clash fails here, not on a thread
        threading.Thread(target=self._loop, name=f"pipe-{self.name}", daemon=True).start()
        return self

    def close(self) -> None:
        self._stop.set()

    def _create(self) -> int:
        flags = PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | (FILE_FLAG_FIRST_PIPE_INSTANCE if self._first else 0)
        h = kernel32.CreateNamedPipeW(_path(self.name), flags,
                                      PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS,
                                      PIPE_UNLIMITED_INSTANCES, 1 << 16, 1 << 16, 0, ctypes.byref(self._sa))
        if h in (None, INVALID_HANDLE):
            raise ctypes.WinError(ctypes.get_last_error(), f"CreateNamedPipe({self.name})")
        self._first = False
        return h

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                h = self._handle if self._handle is not None else self._create()
                self._handle = None
            except OSError:
                log.exception("pipe %s: can't create an instance", self.name)
                self._stop.wait(5)
                continue
            if not self._wait_for_client(h):
                kernel32.CloseHandle(h)
                return
            conn = PipeConnection(h)
            try:
                ident = client_identity(conn)
            except OSError as e:
                log.warning("pipe %s: client vanished before it could be identified: %s", self.name, e)
                conn.close()
                continue
            try:
                self._on_client(conn, ident)
            except Exception:
                log.exception("pipe %s: on_client failed", self.name)
                conn.close()

    def _wait_for_client(self, h) -> bool:
        ov = _winapi.ConnectNamedPipe(h, overlapped=True)
        try:
            while True:
                if _winapi.WaitForMultipleObjects([ov.event], False, 500) != WAIT_TIMEOUT:
                    break
                if self._stop.is_set():
                    ov.cancel()
                    return False
        finally:
            _, err = ov.GetOverlappedResult(True)
        return err == 0 and not self._stop.is_set()
