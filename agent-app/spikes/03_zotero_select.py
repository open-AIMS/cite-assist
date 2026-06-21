"""Spike 03 — zotero://select actually selects the item (A13).

Risk: the "Open in Zotero" button builds a `zotero://select/...` URI from the
record's `zotero_key` plus the library type/id, and hands it to the OS. This
must (a) be built correctly for user vs group libraries and (b) actually make
the running Zotero desktop jump to the item.

This spike builds the URI and launches it. It needs the Zotero desktop app
running. It will try to auto-discover a real `zotero_key` from the local cache
(~/.cite-assist/zotero-cache/records/*.md); otherwise set ZOTERO_KEY.

Environment overrides:
    ZOTERO_KEY            a real item key (else auto-discovered from the cache)
    ZOTERO_LIBRARY_TYPE   "user" (default) or "group"
    ZOTERO_LIBRARY_ID     group id when type is "group" (default "0")

Run from the repo root:
    python spikes/03_zotero_select.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "agent-cli"))


def discover_key() -> str:
    records = Path.home() / ".cite-assist" / "zotero-cache" / "records"
    if not records.is_dir():
        return ""
    try:
        from refcite import store
    except Exception:
        return ""
    for rec in store.load_records(records):
        if rec.zotero_key:
            print(f"discovered key {rec.zotero_key} from {rec.path}")
            return rec.zotero_key
    return ""


def build_uri(library_type: str, library_id: str, key: str) -> str:
    if library_type == "group":
        return f"zotero://select/groups/{library_id}/items/{key}"
    return f"zotero://select/library/items/{key}"


def main() -> int:
    key = os.environ.get("ZOTERO_KEY") or discover_key()
    if not key:
        print("No zotero_key found. Set ZOTERO_KEY or sync the cache first.",
              file=sys.stderr)
        return 2
    library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")
    library_id = os.environ.get("ZOTERO_LIBRARY_ID", "0")

    uri = build_uri(library_type, library_id, key)
    print("URI:", uri)
    print("Launching... (Zotero desktop must be running; confirm it jumps to the item)")
    opened = webbrowser.open(uri)
    print("webbrowser.open returned:", opened)
    print("\nVERDICT: visually confirm Zotero selected the item. "
          "If nothing happened, check the library type/id and that the "
          "zotero:// protocol handler is registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
