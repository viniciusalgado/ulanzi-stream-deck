"""Run a function as a Windows service (the process the Service Control Manager started).

    run_as_service("UlanziDeck", body)     # body(stop: threading.Event) runs until stop is set; blocks

Plain ctypes, no pywin32: StartServiceCtrlDispatcher hands this process to the SCM, ServiceMain reports
RUNNING and calls body, and Stop/Shutdown set `stop`. If body raises, the service stops with exit code 1, so
the SCM's recovery actions (set up by deck.deploy) restart it. The process must be started by the SCM: a venv's
python.exe is a launcher that runs the real interpreter as a child, which the SCM can't talk to, so deck.deploy
copies a real interpreter into Program Files.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

SERVICE_WIN32_OWN_PROCESS = 0x10
STOPPED, START_PENDING, STOP_PENDING, RUNNING = 1, 2, 3, 4
ACCEPT_STOP, ACCEPT_SHUTDOWN = 0x1, 0x4
CONTROL_STOP, CONTROL_INTERROGATE, CONTROL_SHUTDOWN = 1, 4, 5
NO_ERROR, ERROR_CALL_NOT_IMPLEMENTED = 0, 120


class _SERVICE_STATUS(ctypes.Structure):
    _fields_ = [("dwServiceType", wintypes.DWORD), ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD), ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD), ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD)]


_MAIN = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
_HANDLER = ctypes.WINFUNCTYPE(wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)


class _SERVICE_TABLE_ENTRYW(ctypes.Structure):
    _fields_ = [("lpServiceName", wintypes.LPWSTR), ("lpServiceProc", _MAIN)]


advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE
advapi32.RegisterServiceCtrlHandlerExW.argtypes = [wintypes.LPCWSTR, _HANDLER, ctypes.c_void_p]
advapi32.SetServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(_SERVICE_STATUS)]
advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(_SERVICE_TABLE_ENTRYW)]

_keep = []   # the callbacks must outlive the dispatcher


def run_as_service(name: str, body: Callable[[threading.Event], None]) -> None:
    stop = threading.Event()
    status = _SERVICE_STATUS(SERVICE_WIN32_OWN_PROCESS, START_PENDING, 0, NO_ERROR, 0, 0, 10_000)
    handle = [None]

    def report(state: int, exit_code: int = NO_ERROR, wait_hint: int = 0) -> None:
        status.dwCurrentState = state
        status.dwControlsAccepted = 0 if state in (START_PENDING, STOP_PENDING, STOPPED) else ACCEPT_STOP | ACCEPT_SHUTDOWN
        status.dwWin32ExitCode = exit_code
        status.dwCheckPoint = 0 if state in (RUNNING, STOPPED) else status.dwCheckPoint + 1
        status.dwWaitHint = wait_hint
        if handle[0]:
            advapi32.SetServiceStatus(handle[0], ctypes.byref(status))

    def handler(control, event_type, event_data, context):
        if control in (CONTROL_STOP, CONTROL_SHUTDOWN):
            log.info("service: %s requested", "stop" if control == CONTROL_STOP else "shutdown")
            report(STOP_PENDING, wait_hint=15_000)
            stop.set()
            return NO_ERROR
        if control == CONTROL_INTERROGATE:
            return NO_ERROR
        return ERROR_CALL_NOT_IMPLEMENTED

    def main(argc, argv):
        handle[0] = advapi32.RegisterServiceCtrlHandlerExW(name, handler_cb, None)
        if not handle[0]:
            log.error("service: RegisterServiceCtrlHandlerEx failed (%s)", ctypes.get_last_error())
            return
        report(RUNNING)
        code = NO_ERROR
        try:
            body(stop)
        except Exception:
            log.exception("service: crashed")
            code = 1   # nonzero: the SCM treats it as a failure and runs the recovery actions
        report(STOPPED, exit_code=code)

    handler_cb, main_cb = _HANDLER(handler), _MAIN(main)
    _keep.extend([handler_cb, main_cb])
    table = (_SERVICE_TABLE_ENTRYW * 2)(_SERVICE_TABLE_ENTRYW(name, main_cb), _SERVICE_TABLE_ENTRYW(None, _MAIN()))
    if not advapi32.StartServiceCtrlDispatcherW(table):
        raise ctypes.WinError(ctypes.get_last_error(), "StartServiceCtrlDispatcher (not started by the SCM?)")
