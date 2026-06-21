"""Throwaway spike helper: enrich a Zotero item by key.
Prints citation metadata + abstract, then walks to its PDF attachment(s) and
prints a full-text snippet. Demonstrates the parent -> children -> fulltext walk.
Usage: python zenrich.py <itemKey> [snippetChars]
Scratch probe only; not the skill deliverable.
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:23119/api/users/0"


def get(path):
    with urllib.request.urlopen(f"{API}/{path}") as r:
        return r.read()


def jget(path):
    return json.loads(get(path))


def main(key, snip=400):
    it = jget(f"items/{key}")
    d = it["data"]
    print(f"key={key} type={d.get('itemType')}")
    print(f"title: {d.get('title')}")
    creators = "; ".join(
        f"{c.get('lastName', c.get('name', ''))}, {c.get('firstName', '')}".strip(", ")
        for c in d.get("creators", [])
    )
    print(f"creators: {creators}")
    print(f"date: {d.get('date')}  container: {d.get('publicationTitle') or d.get('publisher') or ''}")
    print(f"doi: {d.get('DOI', '')}  url: {d.get('url', '')}")
    ab = (d.get("abstractNote") or "").strip().replace("\n", " ")
    print(f"abstract: {ab[:300]}")
    # walk to attachments
    kids = jget(f"items/{key}/children")
    pdfs = [k for k in kids if k["data"].get("contentType") == "application/pdf"]
    print(f"children: {len(kids)}  pdf-attachments: {len(pdfs)}")
    for att in pdfs:
        ak = att["key"]
        try:
            ft = jget(f"items/{ak}/fulltext")
            content = (ft.get("content") or "").strip().replace("\n", " ")
            print(f"  [{ak}] fulltext {len(content)} chars: {content[:snip]}")
        except Exception as e:
            print(f"  [{ak}] no fulltext ({e})")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 400)
