# Cite-Assist — Design Document

Status: Implemented

A workflow that helps an author add citations to report prose, drawing on a
Zotero library. An agent (in VS Code) drives a small command-line tool that
syncs a local cache of the library, searches it, and writes human-reviewable
handoff files. Output citations are **APA style**.

---

## 1. Goals and non-goals

**Goals**
- Agent-driven workflow: author selects text, agent finds and proposes citations.
- Easy setup for others: baseline runs on system Python 3.11+ with **no third-party
  packages**.
- Works offline against a cached copy of the library; Zotero only needed to *update*
  the cache.
- Search that is **conceptual, not just keyword** — but degrades gracefully to a
  pure-stdlib method when no model server is available.
- Final document uses **APA in-text citations and an APA reference list**.
- Architecture is staged and extensible toward embedding / RAG search.

**Non-goals**
- No VS Code extension (unnecessary complexity; the agent shells out to the CLI).
- No LaTeX / pandoc `[@key]` citekeys, no Better BibTeX dependency.
- No OCR of scanned PDFs (flagged, not processed).

---

## 2. Key design decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Ship as an **agent spec + a single Python package** run via CLI, not an extension. | Matches the existing `update-reference-doc` pattern; trivial for others to run. |
| D2 | **Baseline has zero third-party dependencies.** | Anyone with system Python can run it; no install friction. |
| D3 | **Each cached record stores full CSL citation metadata in its front matter.** | Single source of truth for APA generation; tool works with Zotero closed. |
| D4 | **No Better BibTeX / pandoc citekeys.** Use an internal record id; generate APA from stored metadata. | User wants APA, not `[@key]`. Removes a plugin dependency. |
| D5 | **PDF text comes from Zotero's own full-text API first**, PyMuPDF only as an optional fallback. | Zotero already extracts and indexes PDF text; avoids a parsing dependency. |
| D6 | **Search is a pluggable backend behind one interface.** Default = stdlib BM25. Optional = embeddings via a local model server (Ollama / LM Studio). | Conceptual search where a server exists; always-works fallback where it doesn't. |
| D7 | **Embeddings are obtained over HTTP from a local model API**, not from a bundled ML library. | Model swaps with the machine's capability; no `torch`/`sentence-transformers` install. |
| D8 | **Incremental Zotero sync** using the local API's library version (`since`). Large or offline deltas fall back to "search existing cache + warn". | Makes "sync inside the search call" fast and safe. |
| D9 | **Human-in-the-loop review file** is mandatory; insertion is a separate step that **backs up each touched file** before editing. | Citation accuracy and honesty matter for a formal report. There is no true undo, so `insert` saves a pre-edit copy in the session folder and relies on git as the outer safety net. |
| D10 | Claims are typed (background / method / own-project / common-knowledge). **Only common-knowledge claims skip search.** `own-project` claims **are searched**, biased toward the author's own Zotero collections. | A report cites its own datasets and prior milestone reports; skipping them would miss exactly the self-citations a formal report needs. Common knowledge genuinely needs no source. |
| D11 | **Zotero collection/folder is stored per record and usable as an optional search filter.** Default searches all records. | The library has multiple folders; lets the author scope a search without excluding by default. |
| D12 | **One shared `references.md`** for the whole report holds the APA reference list. **Append-only**, deduplicated by `record_id` marker with a normalised-title fallback; the author sorts. | Author preference; a single bibliography across all chapters. Python cannot safely re-sort, so it only appends new, non-duplicate entries. |
| D13 | **LM Studio is the default embeddings server** for Stage 2 (OpenAI-compatible `/v1/embeddings`). | Already installed on the author's machine. |
| D14 | **One human-editable file per session (`review.md`).** All machine files live in a `work/` subfolder. A reference is kept either by naming its candidate code with `insert --select` or by leaving its block and deleting the rest. | Minimises what the author must look at; both the select-by-code and delete-to-reject interactions are simple and survive messy hand-edits. |
| D15 | **Citations are inserted at the end of the sentence (parenthetical APA), by Python.** Narrative form ("Smith (2019) found...") is a manual or agent post-edit. | End-of-sentence placement lets pure-Python insertion skip an agent round trip. Inline rephrasing is left to the author/agent. |
| D16 | **Sessions are folders:** `citations/active/<YYYY-MM-DD_slug>/`. `insert` moves a finished session to `citations/done/`. | Supports concurrent sections, no-headings documents, and a clear "this is finished" signal. Date + agent-suggested slug keeps Windows/OneDrive paths short. |
| D17 | **`record_id` is the stable internal key** linking a cached record, its `review.md` candidate, and its `references.md` entry. Short **codes** (e.g. `C3a`) are conversational handles for talking to the agent. | Titles are long and near-duplicated in the library; a short stable id is a reliable barcode. Codes help the author refer to specific candidates in chat. |
| D18 | **Per-statement AI candidate filter is an optional, pluggable extension.** Baseline = human reviews the top-k directly. | Reading 20 claims x 8 candidates would blow the context budget; the review file's extracts let the human filter without it. The filter can later cull candidates per statement in isolated contexts. |
| D19 | **Long-running operations report live progress to `stderr`** — a single rewriting line on a TTY, periodic newline updates when output is captured. `stdout` stays machine-readable. | A full sync is >1000 sequential HTTP calls and corpus embedding is slow; a silent run looks hung. Routing progress to `stderr` keeps `stdout` parseable for the agent. |
| D20 | **Sync writes each record to disk as it is fetched and flushes the manifest periodically**, rather than buffering the whole library and writing once at the end. | The cache fills visibly during a long sync, and an interrupted run keeps the records it already fetched instead of restarting from nothing. |
| D21 | **Only top-level Zotero items become records; an item's child notes are folded into that record's searchable body**, not ingested as records of their own. | A note is the curator's read of why a source is relevant, so it must be searchable — but under the parent's citation. Treating notes as items produced junk records (note text as the title, blank authors/URL). Fetching `/items/top` excludes child notes and attachments at the source. |
| D22 | **The local API is addressed by IPv4 literal `127.0.0.1`, never `localhost`.** | On Windows, `localhost` resolves to IPv6 `::1` first, but Zotero listens only on IPv4, so every request stalls ~2s waiting for the IPv6 attempt to time out before falling back. At >1000 requests per full sync that is ~30 min of pure connection delay; `127.0.0.1` makes each request ~100x faster (~15 ms). |
| D23 | **`search` is self-contained: it quick-syncs the cache, rebuilds the index if the records changed (mtime-based staleness check), then ranks** — the only command an agent runs after editing `claims.json`. | An agent forgetting the separate `sync`/`index` steps searched a stale index after a sync changed records. Folding sync + conditional reindex into `search` removes that failure mode, without rebuilding when nothing changed (BM25 reindex is instant; the embeddings index re-embeds only the fragments whose text changed — D29). |
| D24 | **Each `review.md` candidate carries one or more generous (~120-word, configurable) extracts and an explicit `Cache:` path to the full cached record.** | BM25 returns false positives; a one-line extract is often too thin to judge relevance. A wider window (plus, per D31, the best few distinct sections) and a direct path to the record markdown lets the human (or the agent, on request) decide without opening Zotero. The window width is set by `[search] extract_words` (default 120). The `<!-- record:NNNN -->` marker is kept for the parser; `Cache:` is the human-readable pointer. |
| D25 | **`new-session` reports the cache state inline (record count + library version), probing Zotero only when the cache is empty; `status` is a manual diagnostic, not a workflow step.** | The agent previously ran `status` first every time. Folding a one-line health summary into session creation removes that redundant step while still surfacing the actionable "cache empty / Zotero unreachable" guidance exactly when it matters. |
| D26 | **`insert --select C1a,C2b` keeps citations by candidate code, pruning the rest from `review.md` in Python**, as the preferred alternative to hand-deleting `###` blocks. Unknown codes are a hard error. | Deleting blocks meant an agent had to reproduce the long, unicode-heavy extract text exactly, which was token-expensive (~50k) and error-prone (curly quotes, ligatures, dashes). Selecting by ASCII code is a single short command, and erroring on a typo means a misremembered code never silently drops a citation. |
| D27 | **The agent proposes the citable statements as a plain numbered list and waits for the author to confirm before any search.** Numbers (`1, 2, ...`) are the handle at this stage; the confirmed order becomes the `C1, C2, ...` codes. | Lets the author add missed statements, drop unwanted ones, or flag uncertain ones up front, before search effort is spent. The `C`-codes do not exist yet, so plain numbers give the author a cheap way to say "keep 1, 3, 4; drop 7; add ...". |
| D28 | **Fragments are size-capped (default ~220 words) and the cap is enforced for every tier, not just `full`.** A whole-paragraph-on-one-line block is split by words. | The ~120-word paragraph splitter only flushed at blank lines, which PDF-extracted bodies rarely contain, so fragments grew to thousands of words and overran the embedding model's context window (e.g. 2048 tokens, producing silent truncation). Some `abstract`-tier records are also mislabelled full text, so the cap must apply regardless of tier. |
| D29 | **The embeddings index is a directory of ~20 shard files (`index/embeddings/shard-NN.json` + `meta.json`), pretty-printed one fragment per line, built incrementally.** Each shard is written as it completes; a rebuild reuses every cached vector whose fragment text is unchanged (matched by a stored `text_hash`) and only re-embeds the rest; `meta.json` is written last to mark completion. | The old single-line `embeddings.json` (~70 MB) could not be opened for inspection and forced a full restart on any interruption. Sharding gives resumable progress, human-readable diffs, and editor-friendly file sizes without producing thousands of files. A model or shard-count change invalidates reuse and triggers a clean re-embed. |
| D30 | **Fragments have a minimum size (`[search] fragment_min_words`, default 30); a sub-minimum chunk is merged into a neighbour — forward by default, backward for a trailing remnant.** | A lone heading line (e.g. "Drivers of coral recovery") followed by a long one-line paragraph was emitted as its own 4-word fragment, which then matched a query and surfaced as a useless heading-only extract. Merging a too-small chunk into the section it introduces guarantees every extract carries enough surrounding context to judge relevance, and attaches headings to their body text. |
| D31 | **Each candidate shows the best few distinct sections of its document (`[search] extracts_per_doc`, default 2), not just the single top fragment.** A backend exposes `record_extracts(query, record_id, n)`, ranking that record's fragments and returning up to `n` near-duplicate-filtered snippets. The embeddings backend memoises the query vector so a `search` + per-candidate `record_extracts` for one claim costs a single embedding call. | A single short extract often aligns with the query yet still leaves a reviewer unsure whether the whole document is on-topic. Showing two genuinely different supporting passages makes a candidate's relevance far easier to confirm or reject from the review file alone. |

---

## 3. Architecture overview

```
.github/agents/
  cite-assist.md                 # agent specification (the workflow)
  cite-assist/
    refcite/                     # the Python package (CLI: python -m refcite)
      __main__.py                # subcommand dispatch
      config.py                  # reads cite-assist.toml
      zotero_sync.py             # incremental sync -> cache
      extract.py                 # fulltext/abstract/meta -> record body
      store.py                   # record store + manifest
      apa.py                     # CSL metadata -> APA in-text + reference
      claims.py                  # data contracts (claims.json + review.md)
      insert.py                  # write citations + reference list into report, session -> done/
      progress.py                # TTY-aware progress reporting for long operations (D19)
      search/
        base.py                  # SearchBackend interface + Fragment/Hit + snippet helpers
        __init__.py              # fragmenting, index build/load, per-claim search orchestration
        bm25.py                  # default, stdlib only
        embeddings.py            # optional: HTTP to local model API
    cite-assist.toml             # user config (one file)
    requirements-optional.txt    # only for PyMuPDF fallback, if wanted
    environment-dev.yml          # conda dev env (pytest) — not needed to run the tool
    tests/                       # pytest suite (dev only)
    README.md
```

The agent never imports Python; it only runs `python -m refcite <cmd>` and reads/writes
the handoff files. Code and agent logic stay cleanly separated and independently reusable.

---

## 4. The cache (works offline)

```
zotero-cache/
  manifest.json                  # library version, per-item hash + content_tier
  records/
    0001.md   0002.md  ...       # one markdown record per library item
  index/
    bm25.json                    # built by `refcite index` (bm25 backend)
    embeddings/                  # built only if embeddings/hybrid backend enabled (D29)
      meta.json                  # model, shard count, fragment total; written last = complete
      shard-00.json ... shard-NN.json   # ~20 shards, one fragment per line, resumable
```

**Record front matter** (extended from the existing navigation block — D3). Holds
everything APA needs, so citations render with Zotero closed:

```yaml
---
record_id: 0001
zotero_key: ABCD1234
item_type: journal-article
authors: ["Smith, J. A.", "Jones, B."]
year: 2019
title: "Mapping turbid-water reefs from satellite imagery"
container: "Remote Sensing of Environment"
volume: "231"
pages: "111-125"
doi: "10.1016/j.rse.2019.01.001"
url: ""
collections: ["Reef mapping", "Remote sensing"]   # Zotero folders (D11)
content_tier: full          # full | abstract | meta
has_fulltext: true
sections: "..."             # navigation index, as in the existing corpus
---
<body: full text fragments, or abstract, or title+tags>
```

`content_tier` drives behaviour: `full` items are fragmented for section-level
retrieval; `abstract`/`meta` items are usually a single fragment. Every fragment is
size-capped so none overruns the embedding model's context window (D28). `meta` items
are marked low confidence and may later be enriched with an abstract fetched by DOI.

---

## 5. Zotero sync (D8)

- Connect to the Zotero desktop local API at `http://127.0.0.1:23119` (IPv4 literal, not
  `localhost` — see D22).
- Read the current library version; compare to `manifest.json`.
  - Unchanged → return immediately.
  - Small delta → fetch only changed items, re-extract those, bump the manifest.
  - Zotero closed, or a large delta → **skip, warn, use existing cache**; tell the user
    to run `refcite sync` manually.
- PDF text per item: try `/items/<key>/fulltext` first (D5); if absent and PyMuPDF is
  installed, extract from the attachment file path; otherwise record `has_fulltext: false`.
- Only **top-level** items become records (`/items/top`); each item's **child notes** are
  stripped of HTML and appended to the parent record's body as a `Curator note:` block so
  they are searchable (D21). Notes never overwrite citation metadata or the `content_tier`.
- Records are written to disk **as each item is fetched**, and the manifest is flushed
  periodically (D20), so a long sync fills the cache visibly and an interrupted run keeps
  what it already fetched.
- The fetch loop (and corpus embedding) prints a **live progress line to `stderr`** (D19);
  `stdout` stays machine-readable.

**Risks / mitigations**
- *Local API shape varies across Zotero versions* → isolate all API calls in
  `zotero_sync.py`; a CSL-JSON export file is an alternative input that needs no live API.
- *Scanned PDFs have no extractable text* → flagged `meta`/`abstract` tier, not OCR'd.

---

## 6. Search core (D6, D7)

One interface, swappable implementation chosen in `cite-assist.toml`:

```python
class SearchBackend:
    def build(self, fragments: list[Fragment]) -> None: ...
    def search(self, query: str, top_k: int) -> list[Hit]: ...
    def record_extracts(self, query, record_id, n) -> list[str]: ...  # best n sections of one doc (D31)
# Fragment(parent_fragment_id, record_id, text, source_path, line_range)
# Hit(record_id, fragment_id, score, snippet, source_path, line_range)
```

Records are split into fragments of ~120 words (paragraph-aligned), hard-capped at ~220
(D28) and floored at ~30 by merging stray short chunks such as lone headings into a
neighbour (D30), so every fragment carries enough context to judge. Each surfaced
candidate then shows the best few **distinct** sections of its document via
`record_extracts`, not just its single top fragment (D31).

- **`bm25` (default, stdlib):** term-overlap + IDF over title/abstract/fulltext fragments.
  Better than grep, zero setup.
- **`embeddings` (optional):** encodes fragments and the query by calling a local model
  API; **LM Studio's OpenAI-compatible `/v1/embeddings` is the default** (D13), Ollama
  `POST /api/embeddings` also supported. Ranks by cosine. Pure HTTP — no ML packages
  installed locally (D7). Server URL + model name in config. The corpus is embedded into
  a sharded, resumable, human-readable index (D29): ~20 shard files under
  `index/embeddings/`, written as each completes, re-embedding only fragments whose text
  changed since the last build. Matching is a brute-force cosine scan over all fragment
  vectors — fine at this corpus size (a few thousand fragments).
- **`hybrid`:** run both, fuse by reciprocal-rank fusion.

An optional `--collection` filter restricts a search to records in one or more Zotero
folders (D11); with no filter, all records are searched. For `own-project` claims the
search is biased toward the author's own collections (configured in `cite-assist.toml`)
rather than restricted to them (D10).

**Tiered confidence (thin records).** Scores are normalised so short `abstract`/`meta`
records are not unfairly penalised against long `full`-text records. Thin tiers are
**never hard-filtered out** on score alone — a weak-but-plausible abstract match still
surfaces, clearly labelled low confidence, and the human shoulders the extra reading.
Only the tier label changes; nothing relevant is silently dropped.

`Fragment` carries `parent_fragment_id` from day one so the future multi-perspective /
doc2query enrichment (extra vectors pointing back to the same fragment) needs **no
interface change** — see Stage 3.

---

## 7. Workflow and data contracts (D9, D10, D14–D18)

```
1. Author selects a block of text in the report (headings optional).
2. Agent proposes the citable statements as a numbered list; author confirms,
   drops or adds statements.
3. Agent writes the confirmed statements ........ -> work/claims.json
4. refcite search (sync + reindex + rank)  ....... -> review.md  (+ work/claims.json)
5. Author + agent choose the candidate codes to keep (or delete rejected blocks).
6. refcite insert [--select C1a,C2b] ............. -> updated report; session -> done/
```

The agent does **not** read every candidate by default (D18). `search` writes the
human-facing `review.md` directly; the only file the author edits is `review.md`.

### Session layout (D14, D16)

```
citations/
  active/
    2026-06-17_project-x-mapped-1023-ree/
      review.md            <- the ONLY file the author edits
      work/                <- machine files; author never needs to open these
        claims.json
        01-Introduction.md   <- pre-edit backup of each touched report file (D9)
  done/
    2026-06-12_methods-shelf-bathymetry/...
```

- **Folder name** = `YYYY-MM-DD_<slug>`. The agent suggests a short slug; the fallback is
  the first ~30 sanitised characters of the selected text. Date only (no time) keeps
  Windows/OneDrive paths short.
- Everything except `review.md`, the report, and the shared `references.md` is safe to
  delete. Stale `active/` sessions are obvious by their date.
- `refcite insert` moves the session folder to `citations/done/` on success.

### Data contracts

- **`work/claims.json`** (machine file) — per claim:
  `{claim_id, code, report_path, line, sentence, claim_type, search_query, existing_citations}`.
  - `claim_type` in {background, method, own-project, common-knowledge}; only
    common-knowledge is **not** searched (D10).
  - `existing_citations` records any in-text citation already on the sentence so `insert`
    never removes or duplicates it. A sentence that already carries a citation is only
    re-emitted as a claim when the extraction agent judges it needs *additional* support.
  - `code` is the short conversational handle (e.g. `C3`); per-candidate codes extend it
    (`C3a`, `C3b`).
- **`review.md`** (the human artefact) — opens with an instructions block and a ready-to-run
  write-back command, then one section per claim. Each claim shows the **sentence in
  context** (a sentence either side); each candidate is a deletable subheading carrying its
  `record_id`, code, proposed APA citation, confidence, tier, a supporting **extract** (so
  most candidates can be judged without opening the source), and a **lookup** (direct
  DOI/URL link, or Title — Creator to find it in Zotero). Delete a candidate subheading to
  reject it; delete all under a claim to leave it uncited. Best-score-below-threshold claims
  are flagged `NEEDS SOURCE` rather than force-fitted. Skipped common-knowledge claims are
  listed as such so nothing silently vanishes.

Example `review.md` claim block:

```markdown
## C3 — line 142
> ...preceding sentence. **Project X mapped 1,023 reefs using the foobar
> classification.** following sentence...

### C3a · (Smith, 2019) · confidence: high · tier: full   <!-- record:0042 -->
Extract: "...we mapped 1,023 individual reef features across the shelf..."
Lookup: Smith, J. A. — *Mapping turbid-water reefs* · https://doi.org/10.1016/...

### C3b · (Jones, 2021) · confidence: medium · tier: abstract   <!-- record:0117 -->
Extract: "...the foobar classification scheme separates..."
Lookup: Jones, B. — *A classification of...* · (no DOI; search Zotero)
```

### Write-back (`refcite insert`)

Triggered either by the copy-paste command at the top of `review.md`, or by asking the
agent to integrate the review (it runs the same command). It needs only the session folder;
the target report path lives in `work/claims.json`.

```
python -m refcite insert "citations/active/2026-06-17_project-x-mapped-1023-ree"
```

`insert`:

1. Backs up each touched report file into `work/` (D9).
2. For each claim, reads which candidate subheadings still exist in `review.md`; keys on the
   **report sentence + line hint**, fuzzy-matches against current prose, and inserts the
   APA in-text citation(s) at the **end of the sentence** (D15). Existing citations on the
   sentence are preserved, never duplicated. No confident prose match → change nothing,
   report it (the report must not be edited between `search` and `insert`).
3. Appends each new reference to the shared **`references.md`** (D12), **append-only**:
   deduplicates by a hidden `record_id` marker, e.g.
   `Smith, J. A. (2019). Mapping turbid-water reefs... <!-- refcite:0042 -->`,
   with a normalised-title fallback for hand-entered references (skip the duplicate and
   backfill the marker). The author sorts the list later, or asks an LLM to.
4. Moves the session folder to `citations/done/`.

APA generation (`apa.py`) reads CSL fields from record front matter; author/year are
normalised to APA form at cache-build time (`extract.py`) so `apa.py` stays a dumb
formatter. Baseline = a small stdlib formatter covering the common item types (journal
article, report, dataset, web). When `meta`-tier metadata lacks author/year it renders a
visible placeholder (e.g. `(Author unknown, n.d.)`) and flags it rather than emitting a
silently broken citation. A fuller CSL engine can be added later as an optional backend.

### Deduplication (search)

The search tool collapses duplicates best-effort so the human sees each source once per
claim: the same record matched via multiple fragments → one candidate (best-scoring
fragment); near-duplicate library items → matched by normalised title, shown once. The same
source legitimately cited by two different sentences is **not** a duplicate. The agent and
human catch any remaining slips.

---

## 8. Staging

**Stage 1 — Baseline loop, zero dependencies**
- `sync` (local API + CSL-JSON fallback), tiered record store with extended front matter,
  `index` + `search` with **BM25**, the single-editable `review.md` + `work/` session
  contracts, `insert` with **APA** in-text + append-only reference list. The full workflow
  works; the human reviews the top-k directly (no agent narrowing).

**Stage 2 — Conceptual search via local model API**
- `embeddings` backend over Ollama / LM Studio HTTP; `hybrid` fusion. Backend selected in
  config; everything else unchanged. The index is sharded and resumable so a long embed
  run survives interruption and rebuilds re-embed only changed fragments (D28, D29).

**Stage 3 — Multi-perspective enrichment (advanced)**
- `refcite enrich`: a local chat model generates hypothetical questions / claim paraphrases
  per fragment; each is embedded as an extra vector pointing back to the parent fragment.
  No interface change (the `parent_fragment_id` field already exists). Optional Crossref /
  OpenAlex abstract enrichment for `meta`-tier items.

### Extension point — per-statement AI candidate filter (D18)

An optional, pluggable filter that, after `search`, evaluates each statement's candidates
in its own isolated context (local or cloud model via an API) and culls roughly half the
records before the human review. Each statement runs independently, so it is a long task
that reports progress. The tool works identically without it (the human simply sees more
candidates); enabling it trades time/tokens for a shorter review. It plugs into the same
`review.md` contract and changes nothing else.

---

## 9. Author parameters (resolved)

- **Library size:** ~300 records. Small enough that BM25 is instant and full-corpus
  embedding (Stage 2) takes seconds — both backends are comfortably viable.
- **Collections:** multiple Zotero folders; stored per record and offered as an optional
  `--collection` filter (D11). Default search uses all records. An `own_collections` list
  in `cite-assist.toml` names the folders holding the project's own outputs (datasets,
  prior milestone reports) that `own-project` claims are biased toward (D10).
- **Reference list:** one shared `references.md` for the whole report (D12), append-only.
- **Model server:** LM Studio by default for Stage 2 embeddings (D13).

Still worth confirming before build: the rough split of PDF / abstract-only /
metadata-only items, since it sets expectations for how many claims will find strong
full-text support versus abstract-only matches.
```