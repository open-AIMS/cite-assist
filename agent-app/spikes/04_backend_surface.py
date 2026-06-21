"""Spike 04 — in-process backend surface + load_config defaults (A10/A31/A35).

Risk: app mode imports refcite as a local library and calls it in-process. It
also points refcite at the author's *report folder* as the repo root, where
there is usually no `cite-assist.toml`. This spike confirms that, with no toml:

  - the cache resolves user-global (under ~/.cite-assist),
  - sessions + references.md resolve inside the report folder (A10),
  - the declared entry points import and are callable,
  - check_connection returns a dict and never raises when Zotero is absent.

Run from the repo root:
    python spikes/04_backend_surface.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "agent-cli"))


def main() -> int:
    # The declared integration surface (A31). Importing these proves they exist
    # at the documented locations today.
    from refcite.config import load_config
    from refcite import store, zotero_sync
    from refcite.search import run_search, build_index          # noqa: F401
    from refcite.insert import insert_session                    # noqa: F401
    from refcite.claims import Claim, save_claims, claims_template  # noqa: F401

    report_folder = Path(tempfile.mkdtemp(prefix="citeapp-report-"))
    cfg = load_config(repo_root=report_folder)

    home = Path.home().resolve()
    cache_under_home = home in cfg.cache_dir.resolve().parents or \
        str(cfg.cache_dir).startswith(str(home))
    sessions_in_report = report_folder in cfg.sessions_dir.resolve().parents or \
        cfg.sessions_dir.resolve() == (report_folder / "citations").resolve()
    refs_in_report = cfg.references_file.resolve() == (report_folder / "references.md").resolve()

    print("repo root  :", cfg.repo_root)
    print("cache dir  :", cfg.cache_dir, "  user-global:", cache_under_home)
    print("sessions   :", cfg.sessions_dir, "  in report folder:", sessions_in_report)
    print("references :", cfg.references_file, "  in report folder:", refs_in_report)
    print("backend    :", cfg.backend)

    records = store.load_records(cfg.records_dir)
    print("records    :", len(records))

    conn = zotero_sync.check_connection(cfg)
    print("check_connection ->", conn)
    conn_ok = isinstance(conn, dict) and "ok" in conn

    ok = cache_under_home and sessions_in_report and refs_in_report and conn_ok
    print("\nVERDICT:", "PASS" if ok else "FAIL — review the paths above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
