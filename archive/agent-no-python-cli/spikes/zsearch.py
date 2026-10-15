"""Throwaway spike helper: search the Zotero local API and print ranked candidates.
Usage: python zsearch.py "query terms" [qmode] [limit]
Not part of the skill deliverable; a scratch probe only.
"""
import json
import sys
import urllib.parse
import urllib.request

API = "http://127.0.0.1:23119/api/users/0"


def search(q, qmode="everything", limit=6):
    url = f"{API}/items?q={urllib.parse.quote(q)}&qmode={qmode}&limit={limit}&itemType=-attachment"
    with urllib.request.urlopen(url) as r:
        items = json.load(r)
    print(f'### query: "{q}" (qmode={qmode}) -> {len(items)} hits')
    for i, it in enumerate(items, 1):
        d = it["data"]
        m = it.get("meta", {})
        creator = m.get("creatorSummary", "?")
        year = (m.get("parsedDate") or "n.d.")[:4]
        title = (d.get("title") or "")[:85]
        print(f"  {i}. {it['key']} [{d.get('itemType')}] {creator} ({year}) - {title}")
    print()


if __name__ == "__main__":
    q = sys.argv[1]
    qmode = sys.argv[2] if len(sys.argv) > 2 else "everything"
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    search(q, qmode, limit)
