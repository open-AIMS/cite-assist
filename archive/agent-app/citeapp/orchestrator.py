"""orchestrator.py — the deterministic glue between the LLM calls (A3, A19, A32).

The control flow of app mode is fixed Python, not model reasoning (Lesson L1).
This module owns the **non-LLM** half of the loop that happens once the author
has confirmed their statements and the model has produced claims: scaffold a
session, write ``work/claims.json``, and run the existing ``run_search`` to
produce ``review.md`` plus the structured candidates the GUI renders (A32).

It deliberately accepts already-generated :class:`refcite.Claim` objects (the
LLM work is done in :mod:`citeapp.tasks`) so this stitching is unit-testable
without a model, and takes ``run_search_fn``/``create_session_fn`` injection
points so a test can stand in lightweight fakes.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from refcite import Claim, create_session, run_search, save_claims

from . import tasks

# Ephemeral paste sessions live here, one random subfolder each (A37). It sits
# under ``~/.cite-assist`` alongside the rest of the app-owned data (A17).
SCRATCH_ROOT = Path(os.path.expanduser("~/.cite-assist/scratch"))


def create_scratch_document(text: str, *, scratch_root: Path = SCRATCH_ROOT) -> Path:
    """Mint an ephemeral scratch project folder for a pasted session (A36/A37).

    A pasted session has no author-chosen folder to act as the project root
    (A10), so the app mints one under ``~/.cite-assist/scratch/<random>/`` and
    writes the pasted prose into a ``pasted.md`` working document inside it. The
    document's parent folder becomes the project root passed to ``load_config``
    (``references.md`` and ``citations/`` land there), and the author copies the
    results out of the result tabs (A38) rather than revisiting the folder.

    Returns the path to the working ``.md`` document.
    """
    scratch_root.mkdir(parents=True, exist_ok=True)
    folder = scratch_root / secrets.token_hex(4)
    while folder.exists():                       # avoid the (tiny) collision chance
        folder = scratch_root / secrets.token_hex(4)
    folder.mkdir(parents=True)
    document = folder / "pasted.md"
    document.write_text(text, encoding="utf-8")
    return document


def build_session(cfg, claims: list[Claim], report_path, *,
                  on_progress=None, create_session_fn=create_session,
                  run_search_fn=run_search) -> dict:
    """Scaffold a session, write its claims, and rank them.

    Returns ``{"session_dir": Path, "summary": dict}`` where ``summary`` is the
    ``run_search`` return (including the structured ``candidates`` list, A32).
    """
    report_path = Path(report_path)
    session_dir = create_session_fn(cfg, report_path.stem)

    payload = tasks.claims_to_dict(session_dir.name, str(report_path), claims)
    save_claims(session_dir / "work" / "claims.json", payload)

    summary = run_search_fn(cfg, session_dir, verbose=False, on_progress=on_progress)
    return {"session_dir": session_dir, "summary": summary}
