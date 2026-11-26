"""Who is at the PC: the console session, its user, whether it's locked; and starting a SYSTEM process in it.

    c = console()                 # Console(session, user, locked); session is None between sessions
    own_session()                 # the Windows session this process runs in
    child = spawn_system(session, ["pythonw.exe", ...])   # service only: a SYSTEM process in that session
    child.alive(); child.kill(); child.pid

The console session owns the monitors. When nobody is signed in it's the login screen's session (user "");
it changes when someone signs in or out. spawn_system needs SeTcbPrivilege (LocalSystem has it): it copies the
service's own token, moves the copy into the target session, and starts the process on that session's
login-screen desktop when nobody is signed in, or on the user's desktop otherwise.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from dataclasses import dataclass

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

NO_SESSION = 0xFFFFFFFF
WTS_USER_NAME, WTS_SESSION_INFO_EX = 5, 25
WTS_SESSIONSTATE_LOCK = 0


@dataclass(frozen=True)
class Console:
    session: int | None = None   # None: between sessions (someone is signing in or out)
    user: str = ""               # "" at the login screen
    locked: bool = False


class _WTSINFOEX_LEVEL1(ctypes.Structure):
    _fields_ = [("SessionId", wintypes.ULONG), ("SessionState", ctypes.c_int), ("SessionFlags", wintypes.LONG),
                ("WinStationName", wintypes.WCHAR * 33), ("UserName", wintypes.WCHAR * 21),
                ("DomainName", wintypes.WCHAR * 18), ("LogonTime", ctypes.c_longlong),
                ("ConnectTime", ctypes.c_longlong), ("DisconnectTime", ctypes.c_longlong),
                ("LastInputTime", ctypes.c_longlong), ("CurrentTime", ctypes.c_longlong),
                ("IncomingBytes", wintypes.DWORD), ("OutgoingBytes", wintypes.DWORD),
                ("IncomingFrames", wintypes.DWORD), ("OutgoingFrames", wintypes.DWORD),
                ("IncomingCompressedBytes", wintypes.DWORD), ("OutgoingCompressedBytes", wintypes.DWORD)]


class _WTSINFOEX(ctypes.Structure):
    _fields_ = [("Level", wintypes.DWORD), ("Data", _WTSINFOEX_LEVEL1)]


wtsapi32.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                 ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD


def _query(session: int, info: int) -> bytes | None:
    buf, size = ctypes.c_void_p(), wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(None, session, info, ctypes.byref(buf), ctypes.byref(size)):
        return None
    try:
        return ctypes.string_at(buf, size.value)
    finally:
        wtsapi32.WTSFreeMemory(buf)


def console() -> Console:
    session = kernel32.WTSGetActiveConsoleSessionId()
    if session == NO_SESSION:
        return Console()
    raw = _query(session, WTS_USER_NAME)
    user = raw.decode("utf-16-le").rstrip("\0") if raw else ""
    locked = False
    if user:
        ex = _query(session, WTS_SESSION_INFO_EX)
        if ex and len(ex) >= ctypes.sizeof(_WTSINFOEX):
            info = _WTSINFOEX.from_buffer_copy(ex)
            locked = info.Level == 1 and info.Data.SessionFlags == WTS_SESSIONSTATE_LOCK
    return Console(session, user, locked)


def own_session() -> int:
    s = wintypes.DWORD()
    kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(s))
    return s.value


def presence_here() -> str:
    """For a process inside the user's session: "locked" when its session is the locked console, else "here"."""
    c = console()
    return "locked" if c.session == own_session() and c.locked else "here"


# ---------------- starting a SYSTEM process in another session (service only) ----------------

TOKEN_ALL_ACCESS = 0xF01FF
MAXIMUM_ALLOWED = 0x02000000
SECURITY_IMPERSONATION, TOKEN_PRIMARY, TOKEN_SESSION_ID = 2, 1, 12
CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT = 0x08000000, 0x00000400
STILL_ACTIVE = 259


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.DuplicateTokenEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                      ctypes.POINTER(wintypes.HANDLE)]
advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
advapi32.CreateProcessAsUserW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                          ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
                                          ctypes.POINTER(_PROCESS_INFORMATION)]
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


class Child:
    def __init__(self, pid: int, handle):
        self.pid = pid
        self._handle = handle

    def alive(self) -> bool:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code))) and code.value == STILL_ACTIVE

    def kill(self) -> None:
        if self._handle:
            kernel32.TerminateProcess(self._handle, 1)
            kernel32.CloseHandle(self._handle)
            self._handle = None


def _check(ok, what):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


def spawn_system(session: int, argv: list[str], *, signed_in: bool, cwd: str | None = None) -> Child:
    tok, dup = wintypes.HANDLE(), wintypes.HANDLE()
    _check(advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ALL_ACCESS, ctypes.byref(tok)),
           "OpenProcessToken")
    try:
        _check(advapi32.DuplicateTokenEx(tok, MAXIMUM_ALLOWED, None, SECURITY_IMPERSONATION, TOKEN_PRIMARY,
                                         ctypes.byref(dup)), "DuplicateTokenEx")
        sid = wintypes.DWORD(session)
        _check(advapi32.SetTokenInformation(dup, TOKEN_SESSION_ID, ctypes.byref(sid), ctypes.sizeof(sid)),
               "SetTokenInformation(TokenSessionId)")
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = "winsta0\\default" if signed_in else "winsta0\\winlogon"
        pi = _PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        _check(advapi32.CreateProcessAsUserW(dup, argv[0], cmdline, None, None, False, CREATE_NO_WINDOW, None,
                                             cwd, ctypes.byref(si), ctypes.byref(pi)), "CreateProcessAsUser")
        kernel32.CloseHandle(pi.hThread)
        return Child(pi.dwProcessId, pi.hProcess)
    finally:
        kernel32.CloseHandle(tok)
        if dup:
            kernel32.CloseHandle(dup)
