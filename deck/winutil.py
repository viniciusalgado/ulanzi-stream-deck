"""The few Windows calls the deck needs: launch things, focus a window, press keys, one instance only."""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
NO_WINDOW = 0x08000000

VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "esc": 0x1B, "space": 0x20,
    "media_play_pause": 0xB3, "media_next": 0xB0, "media_prev": 0xB1, "volume_mute": 0xAD,
    "volume_up": 0xAF, "volume_down": 0xAE,
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)}, **{str(n): 0x30 + n for n in range(10)},
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
EXTENDED = {0xB3, 0xB0, 0xB1, 0xAD, 0xAF, 0xAE, 0x5B}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _key_events(vks: list[int]) -> list[INPUT]:
    evs = []
    for flags, seq in ((0, vks), (0x0002, list(reversed(vks)))):  # down in order, up in reverse
        for vk in seq:
            i = INPUT(type=1)
            i.u.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags | (0x0001 if vk in EXTENDED else 0), time=0,
                                dwExtraInfo=0)
            evs.append(i)
    return evs


def hotkey(combo: str) -> None:
    """Press a combination like "ctrl+shift+d" in the foreground window."""
    vks = [VK[p] if p in VK else VK[p.upper()] for p in combo.lower().split("+")]
    evs = _key_events(vks)
    arr = (INPUT * len(evs))(*evs)
    if user32.SendInput(len(evs), arr, ctypes.sizeof(INPUT)) != len(evs):
        raise OSError(f"SendInput failed ({ctypes.get_last_error()})")


def media_key(name: str) -> None:
    hotkey(name)


def launch(target: str, *args: str) -> None:
    """Open a URI, file, app path or Start-menu app id (shell:AppsFolder\\...)."""
    if target.lower().startswith("shell:"):
        subprocess.Popen(["explorer.exe", target], creationflags=NO_WINDOW)
    elif args:
        subprocess.Popen([target, *args], creationflags=NO_WINDOW, cwd=os.path.dirname(target) or None)
    else:
        os.startfile(target)


def _window_for_exe(exe_path: str) -> int | None:
    """Main window of a program, by full exe path, or by file name alone (e.g. "Spotify.exe" for Store apps)."""
    by_name = os.path.dirname(exe_path) == ""
    want = os.path.normcase(exe_path if by_name else os.path.abspath(exe_path))
    found: list[int] = []
    proc_cache: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER: skip owned popups
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in proc_cache:
            proc_cache[pid.value] = _exe_of(pid.value)
        exe = proc_cache[pid.value]
        if (os.path.basename(exe) if by_name else exe) == want:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def _exe_of(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.normcase(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def focus_window(exe_path: str) -> bool:
    """Bring the main window of a running program to the front and confirm it got there.

    False if the program isn't running; raises if it runs but Windows wouldn't give it the foreground, so callers
    never type keys into whatever else is in front.
    """
    import time
    hwnd = _window_for_exe(exe_path)
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    for _ in range(4):
        if user32.GetForegroundWindow() == hwnd:
            return True
        # Windows only lets the process that got the last input change the foreground window. A deck press isn't
        # keyboard input, so tap Alt first (the documented way to unlock SetForegroundWindow) ...
        evs = _key_events([VK["alt"]])
        user32.SendInput(len(evs), (INPUT * len(evs))(*evs), ctypes.sizeof(INPUT))
        if not user32.SetForegroundWindow(hwnd):
            # ... and if that wasn't enough, borrow the current foreground thread's input state.
            fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            me = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(me, fg_thread, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(me, fg_thread, False)
        time.sleep(0.08)
    if user32.GetForegroundWindow() == hwnd:
        return True
    raise RuntimeError("Windows didn't let the deck bring the window to the front")


def foreground() -> int:
    return user32.GetForegroundWindow()


def is_foreground(exe_path: str) -> bool:
    """Is that program's main window the one in front right now (so keys pressed now go to it)?"""
    hwnd = _window_for_exe(exe_path)
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


def give_back_focus(hwnd: int) -> None:
    """Return the foreground to a window we took it from (best effort)."""
    if not hwnd or not user32.IsWindow(hwnd):
        return
    evs = _key_events([VK["alt"]])
    user32.SendInput(len(evs), (INPUT * len(evs))(*evs), ctypes.sizeof(INPUT))
    user32.SetForegroundWindow(hwnd)


def dpi_aware() -> None:
    """Work in physical pixels on every monitor, so screenshots and mouse positions line up at any scaling."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:
        pass


def is_running(exe_name: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return exe_name.lower() in out.lower()


def kill(exe_name: str) -> None:
    subprocess.run(["taskkill", "/IM", exe_name, "/F"], capture_output=True, creationflags=NO_WINDOW)


_mutex = None


def single_instance(name: str) -> bool:
    """True if this is the only process holding `name`; keeps the lock until exit."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def release_single_instance() -> None:
    """Drop our handle (after single_instance said no), so the other holder's exit really frees the name."""
    global _mutex
    if _mutex:
        kernel32.CloseHandle(_mutex)
        _mutex = None


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
