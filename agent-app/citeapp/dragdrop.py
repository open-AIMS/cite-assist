"""dragdrop.py — Windows-only file drag-and-drop via ctypes (A21, L4).

Plain stdlib ``tkinter`` has no OS file drag-and-drop on any platform, and the
third-party ``tkinterdnd2`` would break the zero-dependency goal. Windows is the
author's primary platform and Win32 file drop is reachable through ``ctypes``
alone, so app mode enables drag-and-drop **on Windows only** and falls back to
the File-open dialog everywhere else (validated in spike 08).

``enable_drop(window, on_files)`` subclasses the Tk top-level's Win32 window to
handle ``WM_DROPFILES``. It is a no-op (returns ``False``) on non-Windows or if
the wiring fails, so callers never need to branch on the platform themselves.

Two care points from the spike, both load-bearing:

1. ``SetWindowLongPtrW`` / ``CallWindowProcW`` must be given explicit 64-bit-safe
   ``argtypes``/``restype`` (``c_void_p`` / ``c_ssize_t``) or the WNDPROC pointer
   overflows the default 32-bit ``c_int``.
2. The drop target must be ``GetParent(winfo_id()) or winfo_id()`` — ``GetParent``
   returned ``0`` on the tested Tk build, and applying ``DragAcceptFiles`` to the
   wrong window silently rejects drops.
"""

from __future__ import annotations

import sys
from typing import Callable

# Keep references to the installed WNDPROC trampolines alive for the process
# lifetime; if they are garbage-collected the subclassed window crashes.
_KEEPALIVE: list = []


def enable_drop(window, on_files: Callable[[list[str]], None]) -> bool:
    """Enable file drag-and-drop onto ``window`` (a Tk widget). Windows only.

    ``on_files`` is called with the list of dropped file paths. Returns ``True``
    if drop handling was installed, ``False`` otherwise (non-Windows, or a
    failure — the caller silently relies on the File-open dialog instead).
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        return _install(window, on_files)
    except Exception:
        # Any ctypes/Win32 failure degrades to the File-open baseline (L4).
        return False


def _install(window, on_files: Callable[[list[str]], None]) -> bool:
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    WM_DROPFILES = 0x0233
    GWL_WNDPROC = -4
    LRESULT = ctypes.c_ssize_t

    # 64-bit-safe signatures (care point 1).
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                       wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    setter.restype = ctypes.c_void_p
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                       wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT

    child = window.winfo_id()
    hwnd = user32.GetParent(child) or child  # care point 2
    shell32.DragAcceptFiles(hwnd, True)

    # Let drops through the UIPI message filter if the app runs elevated.
    MSGFLT_ALLOW = 1
    filt = getattr(user32, "ChangeWindowMessageFilterEx", None)
    if filt is not None:
        filt.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD,
                         ctypes.c_void_p]
        for win in (hwnd, child):
            for msg in (WM_DROPFILES, 0x004A, 0x0049):  # DROPFILES/COPYDATA/COPYGLOBALDATA
                filt(win, msg, MSGFLT_ALLOW, None)

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)
    state: dict = {}

    def py_wndproc(h, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            paths: list[str] = []
            count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
            for i in range(count):
                length = shell32.DragQueryFileW(wparam, i, None, 0)
                buf = ctypes.create_unicode_buffer(length + 1)
                shell32.DragQueryFileW(wparam, i, buf, length + 1)
                paths.append(buf.value)
            shell32.DragFinish(wparam)
            if paths:
                try:
                    on_files(paths)
                except Exception:
                    pass
            return 0
        return user32.CallWindowProcW(state["old"], h, msg, wparam, lparam)

    new_proc = WNDPROC(py_wndproc)
    state["old"] = setter(hwnd, GWL_WNDPROC, ctypes.cast(new_proc, ctypes.c_void_p))
    _KEEPALIVE.append(new_proc)
    return True
