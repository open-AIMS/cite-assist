"""__main__.py — the ``python -m refcite`` command-line entry point.

Subcommands (see README.md):

    sync          update the offline cache from Zotero (or a CSL-JSON export)
    index         (re)build the search index from the cache
    search        sync, reindex if needed, then rank each claim and (re)write
                  its review.md (the one command an agent needs after editing
                  claims.json; sync and index run automatically)
    insert        write surviving citations into the report; finish the session
    new-session   scaffold a citations/active/<date>_<slug>/ session folder
    status        show cache + config summary

The agent never imports refcite; it only runs these subcommands and reads/writes
the handoff files.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

from . import claims as claims_mod
from . import insert as insert_mod
from . import search as search_pkg
from . import store, zotero_sync
from .config import load_config


def _slugify(text: str, max_len: int = 30) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "session"


def _cmd_sync(cfg, args) -> int:
    zotero_sync.sync(cfg, force_full=args.full, csljson_path=args.from_csljson,
                     limit=args.limit)
    return 0


def _cmd_index(cfg, args) -> int:
    search_pkg.build_index(cfg)
    return 0


def _cmd_search(cfg, args) -> int:
    search_pkg.run_search(
        cfg, args.session,
        do_sync=not args.no_sync,
        collections=args.collection,
    )
    return 0


def _cmd_insert(cfg, args) -> int:
    select = None
    if args.select:
        select = args.select.replace(",", " ").split()
    insert_mod.insert_session(cfg, args.session, select=select)
    return 0


def _cmd_new_session(cfg, args) -> int:
    today = _dt.date.today().isoformat()
    name = f"{today}_{_slugify(args.slug)}"
    session_dir = cfg.active_dir / name
    work_dir = session_dir / "work"
    if session_dir.exists():
        print(f"ERROR: session already exists: {session_dir}", file=sys.stderr)
        return 1
    work_dir.mkdir(parents=True, exist_ok=True)
    template = claims_mod.claims_template(name, args.report or "", today)
    claims_mod.save_claims(work_dir / "claims.json", template)
    print(f"OK: created session {session_dir}")

    # Report the cache state here so the agent does not need a separate `status`
    # call before starting. When the cache is healthy this is a single line and
    # no network call; only an empty cache triggers a Zotero reachability probe
    # and the actionable instructions the agent must relay to the author.
    manifest = store.load_manifest(cfg.manifest_path)
    n_records = len(store.load_records(cfg.records_dir))
    if n_records > 0:
        print(f"  cache: {n_records} records "
              f"(library version {manifest.get('library_version', 0)}) — ready to search.")
    else:
        print("  cache: EMPTY — the library has not been synced yet.")
        conn = zotero_sync.check_connection(cfg)
        if conn["ok"]:
            print("  Zotero is reachable. Build the cache before searching:")
            print('    PYTHONPATH=".github/agents/cite-assist" python -m refcite sync')
        else:
            print(f"  Zotero API UNREACHABLE ({cfg.zotero_api_url}):")
            print(f"    {conn['message']}")

    print(f"  Next: edit {work_dir / 'claims.json'}, then run: "
          f"python -m refcite search \"{session_dir}\"")
    return 0


def _cmd_status(cfg, args) -> int:
    records = store.load_records(cfg.records_dir)
    manifest = store.load_manifest(cfg.manifest_path)
    tiers: dict[str, int] = {}
    for rec in records:
        tiers[rec.content_tier] = tiers.get(rec.content_tier, 0) + 1
    index_file = search_pkg.index_path(cfg)
    index_present = not search_pkg.index_is_stale(cfg)
    index_exists = index_file.exists()
    print(f"config:        {cfg.config_path}")
    print(f"repo root:     {cfg.repo_root}")
    print(f"cache:         {cfg.cache_dir}")
    print(f"records:       {len(records)} "
          f"(library version {manifest.get('library_version', 0)})")
    print(f"tiers:         " + (", ".join(f"{k}={v}" for k, v in sorted(tiers.items())) or "none"))
    print(f"backend:       {cfg.backend}")
    state = "fresh" if index_present else ("stale" if index_exists else "missing")
    print(f"index:         {state} ({index_file})")
    print(f"sessions:      {cfg.sessions_dir}")
    print(f"references:    {cfg.references_file}")
    if not args.no_connect:
        conn = zotero_sync.check_connection(cfg)
        state = "reachable" if conn["ok"] else "UNREACHABLE"
        print(f"zotero API:    {state} ({cfg.zotero_api_url})")
        if not conn["ok"]:
            print(f"  -> {conn['message']}")
        elif conn["library_version"] is not None:
            print(f"  library version {conn['library_version']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="refcite", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="update the offline cache from Zotero")
    p_sync.add_argument("--from-csljson", metavar="FILE",
                        help="build from a CSL-JSON export instead of the live API")
    p_sync.add_argument("--full", action="store_true",
                        help="force a full rebuild rather than an incremental sync")
    p_sync.add_argument("--limit", type=int, metavar="N", default=None,
                        help="only fetch the first N items, to simulate a smaller "
                             "library for fast development; does not prune the rest. "
                             "Use --full to rebuild the whole library.")
    p_sync.set_defaults(func=_cmd_sync)

    p_index = sub.add_parser("index", help="(re)build the search index")
    p_index.set_defaults(func=_cmd_index)

    p_search = sub.add_parser(
        "search",
        help="sync, reindex if stale, then rank claims and write review.md")
    p_search.add_argument("session", help="path to the session folder")
    p_search.add_argument("--no-sync", action="store_true",
                          help="skip the quick Zotero sync before ranking "
                               "(the index is still rebuilt if the records changed)")
    p_search.add_argument("--collection", action="append", metavar="NAME",
                          help="restrict to a Zotero collection (repeatable)")
    p_search.set_defaults(func=_cmd_search)

    p_insert = sub.add_parser("insert", help="write citations into the report")
    p_insert.add_argument("session", help="path to the session folder")
    p_insert.add_argument(
        "--select", metavar="CODES",
        help="comma- or space-separated candidate codes to keep (e.g. C1a,C2b). "
             "Applies exactly those and prunes the rest from review.md, so there "
             "is no need to delete candidate blocks by hand.")
    p_insert.set_defaults(func=_cmd_insert)

    p_new = sub.add_parser("new-session", help="scaffold a new citation session")
    p_new.add_argument("slug", help="short description for the folder name")
    p_new.add_argument("--report", help="default report path for the claims")
    p_new.set_defaults(func=_cmd_new_session)

    p_status = sub.add_parser("status", help="show cache + config summary")
    p_status.add_argument("--no-connect", action="store_true",
                          help="skip the live Zotero local-API connection check")
    p_status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    cfg = load_config()
    try:
        return args.func(cfg, args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
