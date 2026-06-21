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

from pathlib import Path

from refcite import Claim, create_session, run_search, save_claims

from . import tasks


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
