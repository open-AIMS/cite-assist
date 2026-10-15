# Spike Results — No-Python-CLI Agent Mode

Raw outcomes of strategies trialled against the **live** Zotero library. Each
entry: what was tried, the exact command, and what came back. Distilled
keep/drop decisions live in
[`../LESSONS-agent-no-python-cli.md`](../LESSONS-agent-no-python-cli.md).

Environment: Windows, Git Bash, Zotero 8.0.3 running locally, library ~261
top-level items. Date: 2026-06-20.

---

## S1 — Is the local API reachable, and what does it return?

**Command**
```bash
curl -s "http://127.0.0.1:23119/api/users/0/items/top?limit=2&format=json"
```

**Result.** HTTP 200. Returns a JSON array of items; each has `key`, `version`,
`library`, `links`, `meta` (`creatorSummary`, `parsedDate`, `numChildren`), and
a `data` block with CSL-like fields (`itemType`, `title`, `abstractNote`,
`date`, `url`, `place`, creators, etc.). The `0` user-id alias resolves to the
real local library. **Metadata + abstract are available with one call.**

## S2 — Library size / result counts

**Command**
```bash
curl -s -D - -o /dev/null "http://127.0.0.1:23119/api/users/0/items/top?limit=1"
```

**Result.** Header `Total-Results: 261`. Also `X-Zotero-Version: 8.0.3`,
`Last-Modified-Version`, and `Link` rel="next"/"last" for pagination. **Counts
and version are free from headers — no need to page the whole library to size
it.**

## S3 — Keyword search vs full-text ("everything") search

**Command**
```bash
curl -s ".../items?q=coral&qmode=titleCreatorYear&limit=3&format=keys"   # title only
curl -s ".../items?q=coral&qmode=everything&limit=5&format=keys"          # incl. full text
```

**Result.**
- `titleCreatorYear` → `A8GC7YRT, 6K5SXVP8, 3FQIZ34B`
- `everything` → `TQFMI6QT, APK2WWFJ, 9YXNG2PX, A8GC7YRT` (more + different)

The two modes return different sets; `everything` surfaces items that match only
inside the document body. **Server-side full-text search works — this is the
core index-free retrieval primitive.** `format=keys` returns a bare newline list
of keys, ideal for scripting.

## S4 — Retrieving a PDF's full text

**Finding.** `/items/<top-level-key>/fulltext` returns empty / `Not found`. The
`/fulltext` endpoint is on the **attachment** item, not the parent. An item's
`/children` may be only notes (no PDF) → still no full text.

**Working path (two hops):**
```bash
# 1. parent -> attachment keys
curl -s ".../items/<parentKey>/children?format=json" | grep contentType   # find application/pdf
# 2. attachment -> text
curl -s ".../items/<attachmentKey>/fulltext"
```

**Result for a known PDF** (`9YXNG2PX`, parent `A8GC7YRT`):
```json
{ "content": "Multi-Scale Approach for Predicting Fish Species Distributions
across Coral Reef Seascapes\nSimon J. Pittman... Abstract Two of the major
limitations to effective management of coral reef ecosystems..." }
```
Clean, readable extracted text. **Full text is retrievable, but the agent must
walk parent → child attachment → fulltext.**

## S5 — Direct read of zotero.sqlite while Zotero is running

**Command**
```python
sqlite3.connect("file:C:/Users/elawrey/Zotero/zotero.sqlite?immutable=1", uri=True)
```

**Result.** Opens successfully **while Zotero is running** (no lock error).
`SELECT COUNT(*) FROM items` → 485 rows (incl. child notes/attachments).
Full-text tables present: `fulltextItems`, `fulltextWords`, `fulltextItemWords`
(`fulltextItemWords` → 138,766 rows). **Read-only disk access is viable without
closing Zotero**, using the `immutable=1` URI.

**Caveat.** The DB stores full text only as *tokenized words*, not readable
prose — useless for showing the author an extract.

## S6 — On-disk extracted-text cache

**Command**
```bash
find ~/Zotero/storage -name ".zotero-ft-cache" | wc -l            # -> 62
head -c 400 ~/Zotero/storage/9YXNG2PX/.zotero-ft-cache
```

**Result.** 62 `.zotero-ft-cache` files, each holding **readable extracted
text** for one PDF attachment (same content the API `/fulltext` returns). These
are directly greppable:
```bash
rg -l "coral recovery" ~/Zotero/storage/**/.zotero-ft-cache
```
maps a query straight to `storage/<attachmentKey>/`, which links back to an item.
**A pure-disk full-text search is feasible via ripgrep over these caches** — but
coverage is the same 62-PDF subset as the API.

---

## S7 — Query behaviour: `q` is a strict AND word-filter, NOT a relevance ranker

**Tried.** Real claim queries from a prior agent-mode session (`claims.json`),
run via [`zsearch.py`](zsearch.py).

**Result — the headline finding of this round.** A long conceptual query
returns **zero** hits, because `q` requires **every** word to appear somewhere in
the item's indexed content (title + metadata + full text):

```
"Allen Coral Atlas single classification model turbid water northern Australia" -> 0 hits
"Australian Hydrographic Office marine charts navigation reef boundaries"        -> 0 hits
"coral classification turbid"                                                    -> 0 hits
"Allen Coral Atlas turbid"                                                       -> 0 hits
```

Narrow queries work, and the matches are relevant:
```
"turbid"            -> 4 hits (Hammerton & Lawrey 2024 imagery; Stumpf 2003; Morgan 2016; Clark 2017)
"Seamap"            -> 4 hits (Lucieer datasets/webpage; Butler 2017 report)
"bathymetry reef"   -> 4 hits (Coral Sea geomorphology; GBRDEM; ...)
"coral reef mapping"-> 4 hits (GBR Features; Lawrey & Bycroft 2025; ...)
"Landsat"           -> 4 hits (Bishop-Taylor 2021; DEA Intertidal; ...)
"macrotidal"        -> 0 hits (term simply not in any indexed document)
```

**Conclusion.** The API does **not** rank by partial relevance the way BM25 /
embeddings do — it *filters* to items containing all query words, in arbitrary
order. The viable strategy is therefore:
1. Reduce each claim to **a few distinctive terms** (proper nouns, dataset
   names, jargon), not a sentence.
2. Issue **several narrow queries** (one term, or a tight pair) per claim.
3. **Union** the result keys (dedupe by key).
4. The **agent reads metadata/abstract and ranks** — the relevance judgement the
   index used to provide is now the agent's job. The API only supplies the
   candidate pool.

Notes (`itemType=note`) leak into results and must be filtered out client-side
(the `itemType=-attachment` filter does not exclude notes).

## S9 — The parent->child->fulltext walk, as a reusable recipe

**Tried.** [`zenrich.py`](zenrich.py): given an item key, print metadata +
abstract, then walk `/children`, pick `application/pdf` attachments, and fetch
`/items/<attKey>/fulltext`.

**Result.**
- Full-text item (Morgan 2016, `WKJX9C92`): metadata + abstract + **46,457
  chars** of clean full text (snippet-able for extracts).
- Abstract-only dataset (Seamap NBHL, `8IXECYY6`): metadata + abstract, 0 PDF
  children — correctly degrades to abstract tier.

**Conclusion.** A single small helper reliably does enrichment + tiering. The
skill should hand the agent this exact recipe (or let it write this throwaway
script) rather than trusting it to rediscover the two-hop walk each time.

## S11 — Metadata fields per item type (for agent-authored APA)

**Tried.** Dumped raw `data` fields for `report`, `webpage`, `dataset` (plus
`journalArticle` from S9).

**Result — fields available per type:**
- `journalArticle`: `publicationTitle`, `volume`, `issue`, `pages`, `DOI`, `date`
- `report`: `institution`, `place`, `url`, `date`
- `webpage`: `publisher`, `url`, (often no date)
- `dataset`: `repository`, `versionNumber`, `DOI`, `url`, `date`
- Creators are `{creatorType,lastName,firstName}` **or** `{name}` for
  institutional authors (e.g. "Queensland Department of Transport and Main
  Roads") — the APA template must handle both.

**Conclusion.** All fields APA needs are present in the API `data` block, so the
agent can format APA directly from one `/items/<key>` call. The skill must
provide an explicit APA template per item type **and** an institutional-author
case, since there is no `apa.py` to fall back on.

---

## Summary of what is proven

- The **API alone** can: confirm reachability + size (headers), search metadata
  and full text server-side (`qmode=everything`), return metadata/abstract, and
  return clean full text (two-hop). **No index, no cache needed.**
- The **disk** adds: read-only metadata/collections via `immutable` SQLite, and
  greppable full-text caches — useful mainly for collections (own-project bias)
  and exact-match extract locations.
- **Full-text coverage is partial** (62 of 261 items). Most items are
  metadata/abstract only, the same as agent mode's `abstract`/`meta` tiers.

## Spikes still to run

- S8 — token cost of per-claim candidate reading at full session scale (R2):
  partly addressed (search returns compact lines; abstracts ~300 chars; full
  text fetched only when borderline), but not yet measured end-to-end over a
  whole multi-claim session.
- S10 — keyword recall vs agent-mode embeddings on the same claim set (R4): S7
  shows the expected gap (no semantic match; `macrotidal`/paraphrases miss), but
  a side-by-side recall count against the embeddings backend is still to do.
- S12 — `references.md` dedup without a `record_id` marker (R6): provisional key
  = normalised(first-author surname + year + title-prefix); not yet stress-
  tested against near-duplicate library items.

## Scratch helpers (throwaway, not skill deliverables)

- [`zsearch.py`](zsearch.py) — narrow-query search, prints ranked candidate
  metadata.
- [`zenrich.py`](zenrich.py) — metadata + abstract + two-hop full-text walk for
  one item key.
