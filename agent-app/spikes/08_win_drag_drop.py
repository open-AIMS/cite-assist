"""Spike 08 — Windows drag-and-drop via ctypes (A21). WINDOWS ONLY.

Risk: plain stdlib tkinter has no OS file drag-and-drop. The chosen
no-dependency enhancement subclasses the Tk top-level window's Win32 handle
(`DragAcceptFiles` + `WM_DROPFILES`) via `ctypes`. The open questions (design
§10) are whether the window subclasses cleanly and whether wide-char (Unicode)
dropped paths decode correctly. This spike wires it up and prints any dropped
paths. The File-open dialog remains the cross-platform baseline, so a failure
here is non-blocking.

Run on Windows from the repo root, then drag a file (ideally one with spaces or
non-ASCII characters in its name) onto the window:
    python spikes/08_win_drag_drop.py
"""

from __future__ import annotations

import sys


def main() -> int:
    if not sys.platform.startswith("win"):
        print("SKIP: Windows-only spike.", file=sys.stderr)
        return 0

    import ctypes
    import ctypes.wintypes as wintypes
    import tkinter as tk

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    WM_DROPFILES = 0x0233
    GWL_WNDPROC = -4

    # Declare 64-bit-safe signatures. The defaults treat pointers as 32-bit
    # ints, which overflows when a WNDPROC pointer is passed on x64.
    LRESULT = ctypes.c_ssize_t
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                       wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    # SetWindowLongPtrW on 64-bit; SetWindowLongW on 32-bit.
    setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    setter.restype = ctypes.c_void_p
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                       wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT

    root = tk.Tk()
    root.title("Drag a file onto me")
    root.geometry("420x180")
    tk.Label(root, text="Drag a file here (spaces / unicode welcome).\n"
                        "Dropped paths print to the console.").pack(expand=True)

    # The Tk window id is the child HWND; its parent is the top-level frame that
    # receives WM_DROPFILES.
    child = root.winfo_id()
    hwnd = user32.GetParent(child) or child
    try:
        elevated = bool(shell32.IsUserAnAdmin())
    except Exception:
        elevated = False
    print(f"child hwnd={child} top-level hwnd={hwnd} elevated={elevated}")

    shell32.DragAcceptFiles(hwnd, True)

    # Let the drop messages through the UIPI message filter. Without this a
    # process running at a higher integrity level than Explorer (e.g. launched
    # from an elevated terminal) silently rejects drops and the cursor shows the
    # "no drop" circle. Apply to both the top-level and the child window.
    MSGFLT_ALLOW = 1
    filt = getattr(user32, "ChangeWindowMessageFilterEx", None)
    if filt is not None:
        filt.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD,
                         ctypes.c_void_p]
        for win in (hwnd, child):
            for m in (WM_DROPFILES, 0x004A, 0x0049):  # DROPFILES/COPYDATA/COPYGLOBALDATA
                filt(win, m, MSGFLT_ALLOW, None)

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)
    old_proc_ref = {}

    def py_wndproc(h, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
            for i in range(count):
                length = shell32.DragQueryFileW(wparam, i, None, 0)
                buf = ctypes.create_unicode_buffer(length + 1)
                shell32.DragQueryFileW(wparam, i, buf, length + 1)
                print("dropped:", buf.value)
            shell32.DragFinish(wparam)
            return 0
        return user32.CallWindowProcW(old_proc_ref["old"], h, msg, wparam, lparam)

    new_proc = WNDPROC(py_wndproc)
    old_proc_ref["old"] = setter(hwnd, GWL_WNDPROC,
                                 ctypes.cast(new_proc, ctypes.c_void_p))

    print("Window open. Drag a file onto it; close the window to finish.")
    root.mainloop()
    print("\nVERDICT: PASS if dropped paths printed correctly (Unicode intact); "
          "otherwise fall back to File -> Open (baseline).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
