"""zotero_sync.py — keep the offline cache in step with Zotero (DESIGN.md §5).

All Zotero HTTP access is isolated in this module so the rest of refcite never
depends on the exact local-API shape. Two input paths produce the same
``(csl_item, fulltext, collections)`` tuples, which are then upserted into the
record store:

1. The Zotero desktop **local API** (incremental, version-aware).
2. A **CSL-JSON export file** (``File > Export > CSL JSON``) — a dependency-free
   fallback that needs no running Zotero. Collection membership is absent from
   CSL-JSON, so own-collection biasing is unavailable on this path.

Network failures, an unreachable API, or an oversized delta all degrade to
"use the existing cache and warn", never an error.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import extract, store
from .config import Config
from .progress import ProgressReporter
from .store import Record

_FULLTEXT_BATCH = 40

# Zotero's local API is single-threaded and occasionally refuses a connection
# when hammered with the hundreds of sequential requests a full sync makes.
# These transient blips are retried with a short backoff rather than aborting.
_HTTP_RETRIES = 4
_HTTP_BACKOFF = 0.5

# Below this many records the write phase is effectively instant, so we suppress
# the separate "Writing records" bar to avoid noise on small incremental syncs.
_WRITE_PROGRESS_MIN = 50

# Flush the manifest to disk every this many newly written records, so a long
# sync that is interrupted keeps the records it already fetched.
_MANIFEST_SAVE_EVERY = 25

# Shown verbatim whenever the local API is reachable but refuses the request
# (HTTP 403): the "Allow other applications..." setting is off.
ENABLE_LOCAL_API_INSTRUCTIONS = (
    "Zotero's local API is turned off. In Zotero, open Settings -> Advanced and "
    "enable \"Allow other applications on this computer to communicate with "
    "Zotero\", then try again. (While it is off, the local API returns 403 "
    "Forbidden.)"
)


class ZoteroError(Exception):
    """Raised for any unexpected local-API condition; triggers the cache fallback."""


def _warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def diagnose(exc: Exception, url: str = "") -> str:
    """Return a clear, actionable one-line reason for a failed local-API call.

    Distinguishes the three cases the user actually hits: the local API being
    disabled (403), Zotero not running / unreachable, and everything else.
    """
    where = url or "http://127.0.0.1:23119"
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return ENABLE_LOCAL_API_INSTRUCTIONS
        return f"Zotero's local API returned HTTP {exc.code} ({exc.reason})."
    if isinstance(exc, urllib.error.URLError):
        return (
            f"Could not reach Zotero's local API at {where}. Is the Zotero "
            f"desktop app running? Start Zotero and try again."
        )
    return f"Zotero sync failed: {exc}"


def check_connection(cfg: Config, timeout: int = 8) -> dict:
    """Probe the local API without changing the cache.

    Returns ``{"ok": bool, "library_version": int|None, "message": str}``. A
    failure carries a human-actionable ``message`` (e.g. how to enable the API).
    """
    url = f"{_api_base(cfg)}/items?format=versions&since=0&limit=1"
    try:
        _, headers = _http_get(url, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "library_version": None, "message": diagnose(exc, cfg.zotero_api_url)}
    version = headers.get("Last-Modified-Version") or headers.get("last-modified-version")
    return {
        "ok": True,
        "library_version": int(version) if version else None,
        "message": f"Connected to Zotero's local API at {cfg.zotero_api_url}.",
    }


def zotero_select_uri(record: Record, cfg: Config) -> str:
    """Build a ``zotero://select`` deep link for ``record`` (A13).

    Handing the URI to the OS makes a running Zotero desktop jump to the item.
    The path differs for a group library, so it is derived from the config's
    library type/id rather than hard-coded (validated in spike 03).
    """
    key = record.zotero_key
    if cfg.zotero_library_type == "group":
        return f"zotero://select/groups/{cfg.zotero_library_id}/items/{key}"
    return f"zotero://select/library/items/{key}"


# --- public entry point --------------------------------------------------

def sync(cfg: Config, *, force_full: bool = False, csljson_path: str | None = None,
         limit: int | None = None, verbose: bool = True, on_progress=None) -> dict:
    """Update the cache. Never raises for an offline/empty Zotero — warns instead.

    ``on_progress(label, current, total)`` (A22) is an optional callback the GUI
    passes to surface live progress; the CLI passes none and is unaffected.
    """
    if csljson_path:
        return sync_from_csljson(cfg, cfg.resolve(csljson_path), verbose=verbose,
                                 on_progress=on_progress)

    try:
        return _sync_live(cfg, force_full=force_full, limit=limit, verbose=verbose,
                          on_progress=on_progress)
    except (urllib.error.URLError, ZoteroError, OSError, json.JSONDecodeError) as exc:
        reason = diagnose(exc, cfg.zotero_api_url)
        if cfg.csljson_export:
            export = cfg.resolve(cfg.csljson_export)
            if export.is_file():
                _warn(f"{reason} Building from CSL-JSON export {export} instead.")
                return sync_from_csljson(cfg, export, verbose=verbose)
        _warn(f"{reason} Using the existing cache; run `python -m refcite sync` "
              f"again once Zotero is reachable to refresh it.")
        return {"status": "skipped", "reason": reason, "total": _count_records(cfg)}


# --- CSL-JSON file path --------------------------------------------------

def sync_from_csljson(cfg: Config, path: Path, *, verbose: bool = True,
                      on_progress=None) -> dict:
    """Rebuild the cache from a CSL-JSON export file (full, version-agnostic)."""
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("items", [])
    entries = [(item, None, []) for item in items if isinstance(item, dict)]
    if verbose:
        print(f"Building cache from {len(entries)} CSL-JSON items in {path}...",
              file=sys.stderr)
    manifest = store.load_manifest(cfg.manifest_path)
    result = _apply(cfg, manifest, entries, remove_missing=True, verbose=verbose,
                    on_progress=on_progress)
    result["status"] = "ok"
    result["source"] = "csljson"
    if verbose:
        _report(result)
    return result


# --- live local API path -------------------------------------------------

def _sync_live(cfg: Config, *, force_full: bool, limit: int | None = None,
               verbose: bool, on_progress=None) -> dict:
    manifest = store.load_manifest(cfg.manifest_path)
    stored_version = 0 if force_full else int(manifest.get("library_version", 0))

    versions, library_version = _fetch_item_versions(cfg, since=stored_version)
    if not force_full and library_version == stored_version and _count_records(cfg) > 0:
        if verbose:
            print(f"OK: cache already current (library version {library_version}).")
        return {"status": "current", "library_version": library_version,
                "total": _count_records(cfg)}

    changed_keys = list(versions.keys())
    if len(changed_keys) > cfg.sync_max_delta and not force_full and _count_records(cfg) > 0:
        _warn(f"{len(changed_keys)} items changed (> sync_max_delta={cfg.sync_max_delta}); "
              f"skipping. Run `python -m refcite sync --full` to rebuild.")
        return {"status": "skipped", "reason": "large delta",
                "changed": len(changed_keys), "total": _count_records(cfg)}

    # --limit caps how many items are fetched, to simulate a smaller library for
    # fast development. The version is still advanced afterwards (see below) so a
    # follow-up `sync` is a quick no-op and incremental edits are still detected;
    # the un-fetched items are simply treated as absent. A capped run never prunes
    # the records it chose not to fetch. Use `--full` to rebuild the whole library.
    limited = limit is not None and limit > 0 and limit < len(changed_keys)
    if limited:
        changed_keys = changed_keys[:limit]

    collection_names = _fetch_collection_names(cfg)
    total = len(changed_keys)
    if verbose:
        scope = "full library" if stored_version == 0 else f"changes since v{stored_version}"
        if limited:
            scope += f", limited to {total}"
        print(f"Syncing {total} item(s) from Zotero ({scope}, "
              f"library version {library_version})...", file=sys.stderr)

    def _fetch_entries():
        """Fetch each item's CSL metadata, full text, notes and collections
        lazily, so ``_apply`` can write records to disk as they arrive rather
        than buffering the whole library in memory and writing it all at the end."""
        for batch in _chunks(changed_keys, _FULLTEXT_BATCH):
            for item in _fetch_csljson(cfg, batch):
                key = extract._zotero_key(item)
                item_type, collections = _fetch_item_data(cfg, key, collection_names)
                # Standalone notes/attachments are top-level items but are not
                # citable sources; in CSL-JSON they masquerade as `document`, so
                # filter on the real Zotero item type. (Child notes are folded
                # into their parent's body below; see _notes_from_children.)
                if item_type in ("note", "attachment"):
                    continue
                children = _fetch_children(cfg, key)
                fulltext = _fetch_fulltext(cfg, key, children)
                notes = _notes_from_children(children)
                yield (item, fulltext, collections, notes)

    removed_keys = _fetch_deleted(cfg, since=stored_version) if stored_version else []
    # A full rebuild sees the complete top-level set, so anything still cached
    # but no longer present (e.g. notes wrongly ingested by an older version) is
    # pruned. An incremental sync only sees changed items, so it must not prune.
    # A --limit run is partial, so it must not prune either.
    result = _apply(cfg, manifest, _fetch_entries(), total=total,
                    remove_missing=force_full and not limited, remove_keys=removed_keys,
                    verbose=verbose, label="Fetching items from Zotero",
                    progress_min=1, on_progress=on_progress)
    # Advance the version even for a capped run, so the limited cache behaves like
    # a smaller library: a follow-up `sync` is a no-op and only genuine edits are
    # re-fetched. `--full` ignores the version, so it always rebuilds everything.
    manifest["library_version"] = library_version
    store.save_manifest(cfg.manifest_path, manifest)
    result["status"] = "ok"
    result["source"] = "local-api"
    result["library_version"] = library_version
    if verbose:
        _report(result)
    return result


def _api_base(cfg: Config) -> str:
    prefix = "users" if cfg.zotero_library_type == "user" else "groups"
    return f"{cfg.zotero_api_url.rstrip('/')}/api/{prefix}/{cfg.zotero_library_id}"


def _http_get(url: str, *, accept: str = "application/json", timeout: int = 15):
    req = urllib.request.Request(
        url,
        headers={"Accept": accept, "Zotero-API-Version": "3",
                 "User-Agent": "refcite/0.1"},
    )
    # Retry transient connection failures (the local API refusing or dropping a
    # connection under load). HTTPError is a real server response — never retried,
    # since callers rely on e.g. 404 to mean "no full text".
    last_exc: Exception | None = None
    for attempt in range(_HTTP_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), dict(resp.headers)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < _HTTP_RETRIES - 1:
                time.sleep(_HTTP_BACKOFF * (2 ** attempt))
    raise last_exc


def _fetch_item_versions(cfg: Config, *, since: int) -> tuple[dict, int]:
    # /items/top returns only top-level items, so child notes and attachments
    # are excluded. (Plain /items would pull a record's notes in as their own
    # bogus records, taking the note text as the title and leaving the
    # bibliographic fields blank.)
    url = f"{_api_base(cfg)}/items/top?format=versions&since={since}"
    body, headers = _http_get(url)
    try:
        versions = json.loads(body.decode("utf-8")) if body.strip() else {}
    except json.JSONDecodeError as exc:
        raise ZoteroError(f"unexpected versions payload: {exc}") from exc
    header_version = headers.get("Last-Modified-Version") or headers.get("last-modified-version")
    library_version = int(header_version) if header_version else max(
        [int(v) for v in versions.values()] or [since])
    return versions, library_version


def _fetch_csljson(cfg: Config, keys: list[str]) -> list[dict]:
    if not keys:
        return []
    query = urllib.parse.urlencode({"format": "csljson", "itemKey": ",".join(keys)})
    body, _ = _http_get(f"{_api_base(cfg)}/items?{query}")
    data = json.loads(body.decode("utf-8")) if body.strip() else {}
    if isinstance(data, dict):
        return data.get("items", [])
    if isinstance(data, list):
        return data
    raise ZoteroError("unexpected CSL-JSON payload shape")


def _fetch_fulltext(cfg: Config, key: str, children: list[dict] | None = None) -> str | None:
    """Zotero's own extracted full text for an item or its first text attachment (D5)."""
    text = _fulltext_for_key(cfg, key)
    if text:
        return text
    if children is None:
        children = _fetch_children(cfg, key)
    for child_key in _attachment_keys(children):
        text = _fulltext_for_key(cfg, child_key)
        if text:
            return text
    return None


def _fulltext_for_key(cfg: Config, key: str) -> str | None:
    try:
        body, _ = _http_get(f"{_api_base(cfg)}/items/{key}/fulltext")
    except urllib.error.HTTPError:
        return None
    except urllib.error.URLError:
        return None
    if not body.strip():
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    content = data.get("content") if isinstance(data, dict) else None
    return content or None


def _fetch_children(cfg: Config, key: str) -> list[dict]:
    """Return the ``data`` block of every child item (notes + attachments).

    Fetched once per item so full text and notes share a single request.
    """
    try:
        body, _ = _http_get(f"{_api_base(cfg)}/items/{key}/children")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []
    try:
        children = json.loads(body.decode("utf-8")) if body.strip() else []
    except json.JSONDecodeError:
        return []
    return [c.get("data", {}) for c in children
            if isinstance(c, dict) and isinstance(c.get("data"), dict)]


def _attachment_keys(children: list[dict]) -> list[str]:
    return [d.get("key", "") for d in children
            if d.get("itemType") == "attachment" and d.get("key")]


def _notes_from_children(children: list[dict]) -> str:
    """Plain-text of the item's Zotero notes (the curator's relevance read).

    Zotero stores note bodies as HTML; strip the tags and join multiple notes.
    """
    texts = []
    for d in children:
        if d.get("itemType") == "note":
            text = _strip_html(d.get("note") or "")
            if text:
                texts.append(text)
    return "\n\n".join(texts)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    # Preserve block boundaries as newlines before dropping the rest of the tags.
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", raw)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _fetch_collection_names(cfg: Config) -> dict[str, str]:
    try:
        body, _ = _http_get(f"{_api_base(cfg)}/collections")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return {}
    try:
        cols = json.loads(body.decode("utf-8")) if body.strip() else []
    except json.JSONDecodeError:
        return {}
    names = {}
    for col in cols if isinstance(cols, list) else []:
        data = col.get("data", {}) if isinstance(col, dict) else {}
        if data.get("key") and data.get("name"):
            names[data["key"]] = data["name"]
    return names


def _fetch_item_data(cfg: Config, key: str, names: dict[str, str]) -> tuple[str, list[str]]:
    """Return ``(item_type, collection_names)`` for an item from its raw record.

    ``item_type`` is the true Zotero type (e.g. ``note``/``attachment``), which
    CSL-JSON hides behind ``document``; the caller uses it to drop non-citable
    top-level items.
    """
    try:
        body, _ = _http_get(f"{_api_base(cfg)}/items/{key}")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return "", []
    try:
        item = json.loads(body.decode("utf-8")) if body.strip() else {}
    except json.JSONDecodeError:
        return "", []
    data = item.get("data", {}) if isinstance(item, dict) else {}
    item_type = data.get("itemType", "") or ""
    keys = data.get("collections", []) or []
    return item_type, [names.get(k, k) for k in keys]


def _fetch_deleted(cfg: Config, *, since: int) -> list[str]:
    try:
        body, _ = _http_get(f"{_api_base(cfg)}/deleted?since={since}")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []
    try:
        data = json.loads(body.decode("utf-8")) if body.strip() else {}
    except json.JSONDecodeError:
        return []
    return list(data.get("items", []) or [])


# --- shared upsert -------------------------------------------------------

def _record_hash(rec: Record) -> str:
    payload = json.dumps(rec.front_matter, sort_keys=True, ensure_ascii=False) + "\n" + rec.body
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _apply(cfg: Config, manifest: dict, entries,
           *, total: int | None = None, remove_missing: bool,
           remove_keys: list[str] | None = None, verbose: bool = False,
           label: str = "Writing records",
           progress_min: int = _WRITE_PROGRESS_MIN, on_progress=None) -> dict:
    items = manifest.setdefault("items", {})
    key_to_rid = {meta.get("zotero_key"): rid for rid, meta in items.items()
                  if meta.get("zotero_key")}
    seen_keys: set[str] = set()
    written = unchanged = 0

    # A lazy fetch (the live sync) can't tell us the count up front; materialise
    # only in that case so the progress bar still has a denominator.
    if total is None:
        entries = list(entries)
        total = len(entries)

    # Show a bar only when there is enough work to be worth it. The live sync
    # passes progress_min=1 because every item is a slow network round trip even
    # when only a few changed. The GUI callback (A22), when present, fires
    # regardless of the stderr bar's enabled state.
    write_progress = ProgressReporter(
        label, total, enabled=verbose and total >= progress_min,
        on_progress=on_progress)
    write_progress.start()
    for processed, entry in enumerate(entries, 1):
        csl_item, fulltext, collections = entry[0], entry[1], entry[2]
        notes = entry[3] if len(entry) > 3 else None
        zkey = extract._zotero_key(csl_item)
        if not zkey:
            write_progress.update(processed)
            continue
        seen_keys.add(zkey)
        rid = key_to_rid.get(zkey)
        if rid is None:
            rid = store.next_record_id(manifest)
            key_to_rid[zkey] = rid
        rec = extract.make_record(rid, csl_item, fulltext, collections, notes)
        digest = _record_hash(rec)
        prev = items.get(rid)
        if prev and prev.get("hash") == digest and (cfg.records_dir / store.record_filename(rid)).exists():
            unchanged += 1
            write_progress.update(processed, f"{written} written, {unchanged} unchanged")
            continue
        store.write_record(cfg.records_dir, rec)
        items[rid] = {"zotero_key": zkey, "hash": digest, "content_tier": rec.content_tier}
        written += 1
        # Persist periodically so an interrupted long sync keeps the records it
        # already fetched instead of restarting from nothing.
        if written % _MANIFEST_SAVE_EVERY == 0:
            store.save_manifest(cfg.manifest_path, manifest)
        write_progress.update(processed, f"{written} written, {unchanged} unchanged")
    write_progress.done(f"{written} written, {unchanged} unchanged")

    removed = 0
    drop_rids: set[str] = set()
    if remove_missing:
        drop_rids |= {rid for rid, meta in items.items()
                      if meta.get("zotero_key") not in seen_keys}
    for zkey in remove_keys or []:
        rid = key_to_rid.get(zkey)
        if rid:
            drop_rids.add(rid)
    for rid in drop_rids:
        path = cfg.records_dir / store.record_filename(rid)
        if path.exists():
            path.unlink()
        items.pop(rid, None)
        removed += 1

    store.save_manifest(cfg.manifest_path, manifest)
    return {"written": written, "unchanged": unchanged, "removed": removed,
            "total": len(items)}


def _count_records(cfg: Config) -> int:
    if not cfg.records_dir.is_dir():
        return 0
    return sum(1 for _ in cfg.records_dir.glob("*.md"))


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _report(result: dict) -> None:
    print(
        f"OK: sync ({result.get('source', '?')}) — "
        f"{result.get('written', 0)} written, "
        f"{result.get('unchanged', 0)} unchanged, "
        f"{result.get('removed', 0)} removed, "
        f"{result.get('total', 0)} total records."
    )
