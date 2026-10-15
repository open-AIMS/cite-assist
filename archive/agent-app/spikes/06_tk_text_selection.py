"""Spike 06 — Tk Text selection -> line/text, read-only, persistent tag (A9/A11/A30).

Risk: app mode uses a read-only Tk `Text` widget as the editor-of-record. It
must (a) import at all (tkinter is packaged separately on some Linux distros,
A2), (b) yield the exact selected text and its line range (A11), (c) stay
read-only while still selectable (A9), and (d) keep a persistent background tag
on confirmed claim sentences that survives later operations (A30).

This opens a window briefly and auto-closes. Run on a machine with a display.

Run from the repo root:
    python spikes/06_tk_text_selection.py
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import tkinter as tk
    except Exception as exc:  # ImportError on headless/distro-without-tk
        print(f"FAIL: tkinter unavailable ({exc}). "
              f"On Linux: sudo apt install python3-tk", file=sys.stderr)
        return 1

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"SKIP: no display available ({exc}).", file=sys.stderr)
        return 0

    results: dict = {}

    text = tk.Text(root, width=60, height=8)
    text.pack(fill="both", expand=True)
    text.insert("1.0", "Line one.\n"
                       "Project X mapped 1,023 reefs across the shelf.\n"
                       "Line three.")
    text.configure(state="disabled")  # read-only (A9)

    def check():
        # Simulate a selection on line 2 (tag_add works even when disabled).
        text.tag_add("sel", "2.0", "2.end")
        selected = text.get("sel.first", "sel.last")
        first, last = text.index("sel.first"), text.index("sel.last")
        results["selected"] = selected
        results["range"] = (first, last)

        # Persistent highlight (A30): a background tag distinct from the
        # selection that should remain after the selection is cleared.
        text.tag_config("claim", background="#fff3b0")
        text.tag_add("claim", "2.0", "2.end")
        text.tag_remove("sel", "1.0", "end")
        results["claim_ranges"] = text.tag_ranges("claim")

        # Confirm the widget is genuinely read-only: while disabled, a
        # programmatic insert is ignored (Tk silently no-ops rather than
        # raising), so the content must be unchanged afterwards.
        before = text.get("1.0", "end-1c")
        try:
            text.insert("1.0", "X")
        except tk.TclError:
            pass
        after = text.get("1.0", "end-1c")
        results["readonly"] = before == after

        root.destroy()

    root.after(150, check)
    root.mainloop()

    selected = results.get("selected", "")
    first, last = results.get("range", ("", ""))
    print("selected text :", repr(selected))
    print("line range    :", first, "->", last)
    print("claim tag set :", bool(results.get("claim_ranges")))
    print("read-only     :", results.get("readonly"))

    ok = (selected.startswith("Project X")
          and first.startswith("2.")
          and bool(results.get("claim_ranges"))
          and results.get("readonly") is True)
    print("\nVERDICT:", "PASS" if ok else "FAIL — review the values above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
