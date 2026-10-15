"""Spike 07 — worker thread -> Tk marshalling via queue + after() (A22/A26).

Risk: backend operations (sync, search, insert) and LLM calls run on a worker
thread so the Tk event loop stays responsive, and a progress callback (A22)
fires from that worker. Tk is not thread-safe, so updates must be marshalled to
the UI thread. This spike proves the safe pattern: the worker only puts items on
a `queue.Queue`; the UI thread drains it from a periodic `after()` poll and is
the only thread that touches widgets.

This opens a window briefly and auto-closes.

Run from the repo root:
    python spikes/07_tk_worker_thread.py
"""

from __future__ import annotations

import queue
import sys
import threading
import time


def main() -> int:
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"FAIL: tkinter unavailable ({exc}).", file=sys.stderr)
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"SKIP: no display available ({exc}).", file=sys.stderr)
        return 0

    q: "queue.Queue[tuple[str, int, int]]" = queue.Queue()
    seen: list[tuple[str, int, int]] = []
    label = tk.Label(root, text="idle", width=40)
    label.pack(padx=20, pady=20)

    # A22 callback signature: (label, current, total). Called from the worker;
    # it must NOT touch any widget — only enqueue.
    def on_progress(text: str, current: int, total: int) -> None:
        q.put((text, current, total))

    def worker() -> None:
        total = 5
        for i in range(1, total + 1):
            time.sleep(0.1)
            on_progress("Syncing", i, total)
        q.put(("done", total, total))

    def poll() -> None:
        try:
            while True:
                text, current, total = q.get_nowait()
                seen.append((text, current, total))
                label.config(text=f"{text} {current}/{total}")  # UI thread only
                if text == "done":
                    root.after(150, root.destroy)
                    return
        except queue.Empty:
            pass
        root.after(30, poll)

    threading.Thread(target=worker, daemon=True).start()
    root.after(30, poll)
    root.mainloop()

    print("updates received:", seen)
    ok = ("done", 5, 5) in seen and len(seen) >= 6
    print("VERDICT:", "PASS — worker->queue->after() marshalling works"
          if ok else "FAIL — not all updates arrived")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
