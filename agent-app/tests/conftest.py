"""Make the citeapp (agent-app) and shared refcite (agent-cli) packages
importable when pytest is run from this folder.

App mode reuses the shared ``refcite`` CLI, which lives in the sibling
``agent-cli`` implementation folder, and its tests reuse the ``make_config``
helper from ``test_refcite``. Add both locations to ``sys.path``.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # <repo>
for _p in (
    _REPO_ROOT / "agent-cli",          # for `import refcite`
    _REPO_ROOT / "agent-cli" / "tests",  # for `from test_refcite import make_config`
    _REPO_ROOT / "agent-app",          # for `import citeapp`
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
