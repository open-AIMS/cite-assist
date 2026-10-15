"""metrics.py — per-session metrics log for the agent-vs-app comparison.

The research design (``docs/RESEARCH.md`` §3) requires app mode to log
per-session metrics — model, per-call token counts, latencies, claims
proposed/kept, and the run outcome — so the agent-vs-app comparison has
quantitative data. Stage A1 (``DESIGN-app-mode.md``) puts this logging in place
*before any LLM call* so no comparison data is lost once the calls land in
Stages A2–A3.

The log is a single append-only JSON-Lines file under ``~/.cite-assist/`` (A17),
one line per event. Each line is a self-contained JSON object with an ISO
timestamp, a session id, an event name, and an arbitrary payload. Writing is
best-effort: a logging failure must never interrupt the workflow.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .settings import APP_DIR

METRICS_PATH = APP_DIR / "metrics.jsonl"

# A session-mode run is single-threaded through the worker, but the GUI thread
# and worker thread can both log, so guard the append.
_lock = threading.Lock()


class MetricsLog:
    """Append-only per-session metrics writer (one JSONL line per event)."""

    def __init__(self, session_id: str, *, path: Path | None = None):
        self.session_id = session_id
        self.path = path or METRICS_PATH

    def log(self, event: str, **payload) -> None:
        """Append one event line. Best-effort: never raises into the workflow."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session": self.session_id,
            "event": event,
            **payload,
        }
        try:
            line = json.dumps(record, ensure_ascii=False)
            with _lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except (OSError, TypeError, ValueError):
            # Metrics are observational; losing a line must not break the run.
            pass

    def session_started(self, *, model: str, backend: str, report: str) -> None:
        self.log("session_started", model=model, backend=backend, report=report)

    def session_finished(self, *, outcome: str, **payload) -> None:
        self.log("session_finished", outcome=outcome, **payload)
