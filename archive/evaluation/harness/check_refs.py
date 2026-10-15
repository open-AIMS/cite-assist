"""check_refs.py — verify every gold citation in datasets/ exists in the Zotero library.

For each unique (author, year) cited in the tagged gold texts, query the running
Zotero local API and decide whether a matching record exists (author surname /
institution word present AND year matching). Prints a per-reference verdict and a
summary of anything missing or ambiguous.

Usage (Zotero desktop must be running, local API enabled)::

    python harness/check_refs.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from cite_eval import parse_file

API = "http://127.0.0.1:23119/api/users/0"
HARNESS = Path(__file__).resolve().parent
ROOT = HARNESS.parent
DATASETS = ROOT / "datasets"

_STOP = {"the", "of", "and", "for", "de", "department", "dept", "institute",
         "director", "great", "britain"}


def _get(path: str):
    with urllib.request.urlopen(f"{API}/{path}", timeout=10) as fh:
        return json.load(fh)


def _search(term: str, limit: int = 50):
    q = urllib.parse.quote(term)
    url = f"items?q={q}&qmode=titleCreatorYear&limit={limit}&itemType=-attachment"
    try:
        items = _get(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  (search error for '{term}': {exc})")
        return []
    return [it for it in items if it.get("data", {}).get("itemType") != "note"]


def _search_term(author: str) -> str:
    """A distinctive word to search on: first surname, or first non-stopword token."""
    a = author.replace("&", " ").replace(".", " ")
    a = re.split(r"\bet al\b", a)[0]
    tokens = [t for t in re.split(r"[\s,]+", a) if t]
    for t in tokens:
        if t.lower() not in _STOP and len(t) > 2:
            return t
    return tokens[0] if tokens else author


def _author_words(author: str) -> list[str]:
    a = author.replace("&", " ").replace(".", " ")
    a = re.split(r"\bet al\b", a)[0]
    return [t.lower() for t in re.split(r"[\s,]+", a) if t and t.lower() not in _STOP and len(t) > 2]


def _item_year(it: dict) -> str:
    pd = (it.get("meta", {}).get("parsedDate") or "")[:4]
    if pd:
        return pd
    m = re.search(r"\b(\d{4})\b", it.get("data", {}).get("date", "") or "")
    return m.group(1) if m else ""


def _item_creator_blob(it: dict) -> str:
    parts = [it.get("meta", {}).get("creatorSummary", "")]
    for c in it.get("data", {}).get("creators", []):
        parts.append(c.get("lastName", ""))
        parts.append(c.get("name", ""))
    parts.append(it.get("data", {}).get("title", ""))
    return " ".join(parts).lower()


def check_ref(author: str, year: str):
    term = _search_term(author)
    words = _author_words(author)
    cands = _search(term)
    matches = []
    near = []  # author matches but year differs
    for it in cands:
        blob = _item_creator_blob(it)
        author_hit = any(w in blob for w in words)
        if not author_hit:
            continue
        iy = _item_year(it)
        rec = (it["key"], it["data"].get("itemType", "?"),
               it.get("meta", {}).get("creatorSummary", "?"), iy,
               (it["data"].get("title") or "")[:70])
        if iy == year:
            matches.append(rec)
        else:
            near.append(rec)
    return matches, near


def main() -> None:
    refs: dict[tuple[str, str], list[str]] = {}
    for f in sorted(DATASETS.glob("*.md")):
        for sent in parse_file(f):
            for e in sent.pool:
                refs.setdefault((e.author, e.year), []).append(f.name)

    print(f"checking {len(refs)} unique references against Zotero ({API})\n")
    missing, ambiguous = [], []
    for (author, year), where in sorted(refs.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        matches, near = check_ref(author, year)
        tag = f"{author}, {year}"
        used = ", ".join(sorted(set(where)))
        if matches:
            keys = "; ".join(f"{k} [{ct}]" for k, it, ct, iy, t in matches)
            print(f"FOUND     {tag:<55} -> {keys}")
        elif near:
            ambiguous.append(tag)
            opts = "; ".join(f"{ct} {iy} ({k})" for k, it, ct, iy, t in near[:4])
            print(f"YEAR?     {tag:<55} -> author match, other years: {opts}")
        else:
            missing.append(tag)
            print(f"MISSING   {tag:<55} -> no record  (used in {used})")

    print("\n=== summary ===")
    print(f"  found: {len(refs) - len(missing) - len(ambiguous)} / {len(refs)}")
    if ambiguous:
        print(f"  author found but year mismatch ({len(ambiguous)}): " + "; ".join(ambiguous))
    if missing:
        print(f"  MISSING from library ({len(missing)}): " + "; ".join(missing))
    if not missing and not ambiguous:
        print("  all gold references are present in the library.")


if __name__ == "__main__":
    main()
