"""extract.py — turn a CSL-JSON item (+ optional full text) into a Record.

This is where author names and years are normalised to APA form (DESIGN.md §7),
so ``apa.py`` stays a dumb formatter. It also decides the ``content_tier``:

- ``full``     — usable extracted full text (fragmented for retrieval later)
- ``abstract`` — only an abstract is available
- ``meta``     — only title/keywords (low confidence, flagged downstream)
"""

from __future__ import annotations

import re

from .store import Record

# Map CSL types to the internal item_type vocabulary used by apa.py.
_TYPE_MAP = {
    "article-journal": "journal-article",
    "article": "journal-article",
    "article-magazine": "magazine-article",
    "paper-conference": "paper-conference",
    "report": "report",
    "dataset": "dataset",
    "webpage": "webpage",
    "post-weblog": "webpage",
    "book": "book",
    "chapter": "chapter",
    "thesis": "thesis",
}

# Minimum word count for extracted text to count as "full" rather than abstract.
_FULL_TEXT_MIN_WORDS = 60


def _initials(given: str) -> str:
    """``"John A."`` -> ``"J. A."``; handles hyphenated given names."""
    if not given:
        return ""
    parts = [p for p in re.split(r"[\s.]+", given) if p]
    out: list[str] = []
    for part in parts:
        if "-" in part:
            segs = [seg for seg in part.split("-") if seg]
            out.append("-".join(f"{seg[0].upper()}." for seg in segs))
        else:
            out.append(f"{part[0].upper()}.")
    return " ".join(out)


def _csl_authors(raw: list) -> list[str]:
    """Normalise CSL author objects to APA ``"Family, I. N."`` strings."""
    out: list[str] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        literal = (entry.get("literal") or "").strip()
        if literal:
            out.append(literal)
            continue
        family = (entry.get("family") or "").strip()
        given = (entry.get("given") or "").strip()
        if not family and not given:
            continue
        if not family:
            out.append(given)
            continue
        initials = _initials(given)
        out.append(f"{family}, {initials}".rstrip(", ").strip() if initials else family)
    return out


def _csl_year(item: dict):
    issued = item.get("issued") or {}
    date_parts = issued.get("date-parts") or []
    if date_parts and date_parts[0]:
        try:
            return int(date_parts[0][0])
        except (ValueError, TypeError, IndexError):
            pass
    raw = issued.get("raw") or issued.get("literal") or ""
    match = re.search(r"(\d{4})", str(raw))
    return int(match.group(1)) if match else ""


def _map_type(csl_type: str) -> str:
    return _TYPE_MAP.get(csl_type, csl_type or "document")


def csl_to_fields(item: dict) -> dict:
    """Extract the citation metadata fields from a CSL-JSON item."""
    return {
        "zotero_key": _zotero_key(item),
        "item_type": _map_type(item.get("type", "")),
        "authors": _csl_authors(item.get("author") or []),
        "year": _csl_year(item),
        "title": (item.get("title") or "").strip(),
        "container": (item.get("container-title") or "").strip(),
        "publisher": (item.get("publisher") or "").strip(),
        "volume": str(item.get("volume") or "").strip(),
        "issue": str(item.get("issue") or "").strip(),
        "pages": str(item.get("page") or "").strip(),
        "doi": (item.get("DOI") or "").strip(),
        "url": (item.get("URL") or "").strip(),
    }


def _zotero_key(item: dict) -> str:
    """CSL-JSON from Zotero stores the item key in ``id`` (sometimes prefixed)."""
    key = item.get("id") or item.get("key") or ""
    key = str(key)
    # Zotero web-API CSL ids can look like "users/0/items/ABCD1234".
    if "/" in key:
        key = key.rsplit("/", 1)[-1]
    return key


def build_body(item: dict, fulltext: str | None) -> tuple[str, str, bool]:
    """Return (body, content_tier, has_fulltext)."""
    text = (fulltext or "").strip()
    if text and len(text.split()) >= _FULL_TEXT_MIN_WORDS:
        return text, "full", True

    abstract = (item.get("abstract") or "").strip()
    if abstract:
        return abstract, "abstract", False

    title = (item.get("title") or "").strip()
    keywords = item.get("keyword") or ""
    if isinstance(keywords, list):
        keywords = ", ".join(str(k) for k in keywords)
    body = title
    if keywords:
        body = f"{body}\n\nKeywords: {keywords}".strip()
    return body.strip(), "meta", False


def make_record(record_id: str, item: dict, fulltext: str | None,
                collections: list[str] | None,
                notes: str | None = None) -> Record:
    """Assemble a Record from a CSL-JSON item, full text, and collection names.

    ``notes`` carries the curator's Zotero notes for the item (our interpretation
    of why the source is relevant). They are folded into the searchable body so a
    search matches on them, but they do not change the citation metadata or the
    ``content_tier`` of the underlying source.
    """
    fields = csl_to_fields(item)
    body, tier, has_fulltext = build_body(item, fulltext)
    note_text = (notes or "").strip()
    if note_text:
        body = f"{body}\n\nCurator note: {note_text}".strip() if body \
            else f"Curator note: {note_text}"
    front_matter = {"record_id": str(record_id)}
    front_matter.update(fields)
    front_matter["collections"] = list(collections or [])
    front_matter["content_tier"] = tier
    front_matter["has_fulltext"] = has_fulltext
    front_matter["has_notes"] = bool(note_text)
    return Record(record_id=str(record_id), front_matter=front_matter, body=body)
