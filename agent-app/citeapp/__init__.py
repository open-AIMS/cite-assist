"""citeapp — the cite-assist desktop application (app mode).

App mode (``DESIGN-app-mode.md``) is the second way to run the cite-assist
workflow: a lightweight ``tkinter`` GUI owns the orchestration and makes scoped
LLM API calls, where agent mode has an LLM agent drive a generic harness. This
package depends on ``refcite`` through its declared integration surface only
(A31); ``refcite`` never imports ``citeapp``, so the dependency runs one way.

Two launch paths (A16): the thin ``app.py`` launcher and ``python -m citeapp``.
There is deliberately no ``refcite app`` subcommand, which would invert the
dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

# App mode reuses the shared ``refcite`` CLI, which lives in the sibling
# ``agent-cli`` implementation folder. Add it to ``sys.path`` so ``import
# refcite`` resolves wherever the app is launched from (``python app.py`` or
# ``python -m citeapp``). The path is derived from this file's location, so it
# works for any checkout without hard-coding an absolute path.
_AGENT_CLI = Path(__file__).resolve().parent.parent.parent / "agent-cli"
if _AGENT_CLI.is_dir() and str(_AGENT_CLI) not in sys.path:
    sys.path.insert(0, str(_AGENT_CLI))


def main() -> int:
    """Entry point for both ``python -m citeapp`` and ``app.py``."""
    from .gui import run
    return run()
