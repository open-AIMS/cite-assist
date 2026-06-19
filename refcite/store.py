"""store.py — the offline record store and sync manifest (DESIGN.md §4).

A *record* is one markdown file per Zotero item: extended navigation front
matter (full CSL metadata so APA renders with Zotero closed) followed by a body
of full text, an abstract, or just title + keywords depending on ``content_tier``.

The *manifest* tracks the library version and a per-item hash + content tier so
syncs can be incremental and idempotent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlfm

# Stable display order for record front matter (unknown keys are appended).
RECORD_FIELD_ORDER = [
    "record_id",
    "zotero_key",
    "item_type",
    "authors",
    "year",
    "title",
    "container",
    "publisher",
    "volume",
    "issue",
    "pages",
    "doi",
    "url",
    "collections",
    "content_tier",
    "has_fulltext",
    "has_notes",
    "sections",
]


@dataclass
class Record:
    """One cached library item."""

    record_id: str
    front_matter: dict
    body: str
    path: Path | None = None

    @property
    def authors(self) -> list[str]:
        return self.front_matter.get("authors") or []

    @property
    def year(self):
        return self.front_matter.get("year", "")

    @property
    def title(self) -> str:
        return self.front_matter.get("title") or ""

    @property
    def collections(self) -> list[str]:
        return self.front_matter.get("collections") or []

    @property
    def content_tier(self) -> str:
        return self.front_matter.get("content_tier") or "meta"

    @property
    def item_type(self) -> str:
        return self.front_matter.get("item_type") or ""

    @property
    def zotero_key(self) -> str:
        return str(self.front_matter.get("zotero_key") or "")


def record_filename(record_id: str) -> str:
    return f"{record_id}.md"


def write_record(records_dir: Path, rec: Record) -> Path:
    """Write a record to ``<records_dir>/<record_id>.md`` with ordered front matter."""
    records_dir.mkdir(parents=True, exist_ok=True)
    ordered = {k: rec.front_matter[k] for k in RECORD_FIELD_ORDER if k in rec.front_matter}
    for key, value in rec.front_matter.items():
        if key not in ordered:
            ordered[key] = value
    # record_id is always a (zero-padded) string so it round-trips intact.
    ordered["record_id"] = str(rec.record_id)
    path = records_dir / record_filename(rec.record_id)
    path.write_text(yamlfm.make_record(ordered, rec.body), encoding="utf-8")
    rec.path = path
    return path


def read_record(path: Path) -> Record:
    fm, body = yamlfm.parse(path.read_text(encoding="utf-8"))
    record_id = str(fm.get("record_id", path.stem))
    return Record(record_id=record_id, front_matter=fm, body=body, path=path)


def load_records(records_dir: Path) -> list[Record]:
    if not records_dir.is_dir():
        return []
    return [read_record(p) for p in sorted(records_dir.glob("*.md"))]


def records_by_id(records_dir: Path) -> dict[str, Record]:
    return {r.record_id: r for r in load_records(records_dir)}


# --- manifest ------------------------------------------------------------

def empty_manifest() -> dict:
    return {"library_version": 0, "next_record_id": 1, "items": {}}


def load_manifest(path: Path) -> dict:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("library_version", 0)
        data.setdefault("next_record_id", 1)
        data.setdefault("items", {})
        return data
    return empty_manifest()


def save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def next_record_id(manifest: dict) -> str:
    n = int(manifest.get("next_record_id", 1))
    manifest["next_record_id"] = n + 1
    return f"{n:04d}"
