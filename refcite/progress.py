"""progress.py — lightweight, dependency-free progress reporting.

Long operations (a full Zotero sync over hundreds of items, or embedding a whole
corpus) otherwise print nothing until they finish, which looks hung. This module
gives those loops a simple, TTY-aware progress line.

- On an interactive terminal, updates rewrite a single line with a carriage
  return (a live ``[ 120/471] 25% ...`` bar).
- When output is captured (no TTY, e.g. an agent reading the log), it instead
  emits periodic newline-terminated updates so the log stays readable.

All output goes to ``stderr`` so it never pollutes machine-readable ``stdout``.
"""

from __future__ import annotations

import sys
import time


class ProgressReporter:
    """Report progress over a known number of steps.

    Example::

        p = ProgressReporter("Syncing", total=471)
        for i, item in enumerate(items, 1):
            ...
            p.update(i, suffix=f"{written} written")
        p.done("471 items")
    """

    def __init__(self, label: str, total: int, *, enabled: bool = True,
                 stream=None, min_interval: float = 0.2):
        self.label = label
        self.total = max(0, int(total))
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled and self.total > 0
        self.min_interval = min_interval
        self.is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._last_emit = 0.0
        self._last_pct = -1
        self._started = False

    def start(self, suffix: str = "") -> None:
        if not self.enabled:
            return
        self._started = True
        self._emit(0, suffix, force=True)

    def update(self, count: int, suffix: str = "") -> None:
        if not self.enabled:
            return
        if not self._started:
            self._started = True
        now = time.monotonic()
        pct = int(count * 100 / self.total) if self.total else 100
        # Throttle: emit on a percent change, on the last item, or after an
        # interval — whichever comes first.
        is_last = count >= self.total
        if (not is_last and pct == self._last_pct
                and (now - self._last_emit) < self.min_interval):
            return
        self._emit(count, suffix, force=is_last)

    def done(self, suffix: str = "") -> None:
        if not self.enabled:
            return
        # Ensure a final 100% line, then terminate it.
        self._emit(self.total, suffix, force=True)
        if self.is_tty:
            self.stream.write("\n")
        self.stream.flush()

    def _emit(self, count: int, suffix: str, *, force: bool) -> None:
        now = time.monotonic()
        pct = int(count * 100 / self.total) if self.total else 100
        bar = self._render(count, pct, suffix)
        if self.is_tty:
            self.stream.write("\r" + bar + "\033[K")
        else:
            # Non-TTY: only newline-print on percent change or when forced,
            # to keep captured logs from ballooning.
            if not force and pct == self._last_pct:
                return
            self.stream.write(bar + "\n")
        self.stream.flush()
        self._last_emit = now
        self._last_pct = pct

    def _render(self, count: int, pct: int, suffix: str) -> str:
        width = len(str(self.total))
        head = f"{self.label}: [{count:>{width}}/{self.total}] {pct:3d}%"
        return f"{head} — {suffix}" if suffix else head
