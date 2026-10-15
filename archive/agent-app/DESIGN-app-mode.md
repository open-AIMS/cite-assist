# Cite-Assist — App Mode Design Document

Status: Proposed (design agreed; not yet implemented)

A second way to run the cite-assist workflow. Where **agent mode** has an LLM
agent drive the loop through a generic harness (VS Code, Codex, Claude Code) and
shell out to the `refcite` CLI, **app mode** inverts the control: a lightweight
desktop GUI written in pure-stdlib Python owns the orchestration and makes
**targeted LLM API calls** only for the two genuinely intelligent steps. The same
`refcite` backend code does all the deterministic work (sync, search, ranking,
insertion) in both modes.

This document covers app mode only. The core workflow, cache, search backends,
and data contracts are defined in [`DESIGN.md`](../agent-cli/DESIGN.md); app mode reuses them
unchanged.

---

## 1. Motivation and research goal

The repository now ships the agent in **two forms**:

| Mode | Orchestrator | Intelligence source | Harness needed |
|------|--------------|---------------------|----------------|
| **Agent mode** (existing) | An LLM agent in a generic harness | The agent, looping with tools | VS Code / Codex / Claude Code |
| **App mode** (this doc) | Python (the GUI) | Two scoped LLM API calls | Only an OpenAI-compatible LLM API |

**Why build app mode:**

- **Run where only an LLM API is reachable** — no agentic harness required, just a
  chat-completions endpoint.
- **Tolerate weaker models** — the hard control flow (which command to run, in what
  order, with what arguments) is fixed Python, not something a small model must
  reason its way through. The model is asked only to (a) choose citable sentences
  and (b) judge candidate sources.
- **A controlled comparison.** The scientific goal is to compare an *app-based*
  agent against a *harness-based* agent at the **same level of user effort**, to
  learn the strengths and pitfalls of each. App mode therefore deliberately mirrors
  the agent-mode user experience (the author focuses only on sentences and on
  which citations to keep) rather than adding new burdens.

A running **lessons-learned register** ([`docs/LESSONS.md`](../docs/LESSONS.md))
captures observations from this build for the write-up. The research questions,
evaluation method, and model/harness matrix live in
[`docs/RESEARCH.md`](../docs/RESEARCH.md).

**Non-goals**
- App mode is **not** an agentic harness. It will not let the model decide to
  investigate further, fetch more text, or run extra tool calls. Its workflow is
  deliberately rigid (see Lesson L1).
- No new runtime dependencies. GUI = `tkinter`, HTTP = `urllib`, both standard
  library.
- No change to agent-mode *behaviour* or to the `refcite` CLI. The backend gains
  a small, **declared integration surface** (A31) that app mode imports as a
  local library, plus additive, backward-compatible hooks (a progress callback,
  A22; structured candidate results from search, A32; config overrides, A34) and
  the fixed default Zotero URL (D22). Agent mode and the CLI keep working
  unchanged, and `refcite` never imports `citeapp` — the dependency runs one way
  only (A31).

---

## 2. The scoped LLM calls

Everything in the workflow is deterministic Python **except** the genuinely
intelligent steps, which in agent mode the agent performs inline while reasoning
across the whole task. App mode must instead decompose the work up front into a
fixed sequence of scoped LLM API calls:

1. **Statement proposal (LLM call 1).** Given a block of report prose, propose
   which sentences should carry a citation, returned **verbatim**. This is the
   numbered-list step of agent mode (DESIGN.md D27): the author can drop or add
   sentences before any claim metadata is generated. No classification or search
   query is produced yet.

2. **Claim generation (LLM call 2).** Given the author-confirmed set of
   sentences, classify each (`background | method | own-project |
   common-knowledge`) and write a concise conceptual `search_query` per sentence.
   This produces `work/claims.json` (DESIGN.md §7). It is a **separate** call so
   the author can edit the statement set (after call 1) before any intelligence is
   spent on per-claim metadata (A8).

3. **Candidate evaluation (LLM call 3).** Given the generated `review.md` (one
   section per claim, each with candidate sources, extracts, confidence, tier),
   recommend which candidate code(s) to keep per claim, with a one-line rationale,
   **ranking competing candidates against each other**. The model reads
   `review.md` as text and returns candidate codes (e.g. `C3a`); those codes drive
   `insert_session(select=[...])`, which maps them back to records exactly as
   `refcite insert --select` does in agent mode (A33). The GUI itself never parses
   `review.md` \u2014 its recommendations view is rendered from the structured
   candidates `run_search` returns (A32).

Splitting statement selection across calls 1 and 2 mirrors agent mode's
propose → confirm → write-`claims.json` sequence (DESIGN.md D27), and is what lets
the author shape the statement set without doing the classification themselves
(A7). The evaluation (call 3) is given the **whole** `review.md` in one call,
because choosing between candidates is a relative judgement that needs every
candidate in view at once — the same context the agent has in agent mode.

Between calls 2 and 3, the existing `refcite search` (sync + reindex +
BM25/embeddings ranking) runs with no LLM involvement. After call 3, the existing
`refcite insert` writes APA citations into the report and appends to
`references.md`. **App mode adds no intelligence beyond these three calls.**

This decomposition is itself a cost of the app approach (Lesson L5): the harness
lets one model reason fluidly across selection, search and judgement; the app must
fix the call boundaries and the data passed across each one by hand, trading the
model's adaptability for deterministic control.

---

## 3. Key design decisions

| # | Decision | Rationale |
|---|----------|-----------|
| A1 | **Name: "app mode".** The existing mode is "agent mode". | "App" reads as a desktop application; avoids "pilot", which is ambiguous about whether the human or the LLM is piloting. |
| A2 | **Pure standard library: `tkinter` GUI + `urllib` HTTP.** No new `pip` installs. | Matches the project's zero-dependency baseline (DESIGN.md D2). `tkinter` and `urllib` ship with CPython. The only caveat is Linux distros that package `tkinter` separately (`apt install python3-tk`). |
| A3 | **App mode (`citeapp`) imports `refcite` as a local library through a narrow, declared integration surface (A31); it does not shell out. The backend may gain small, additive, backward-compatible hooks (a progress callback, A22; structured candidate results, A32; config overrides, A34), but agent mode and the CLI keep working unchanged.** | In-process calls (`run_search`, `insert_session`, `sync`) are cleaner and faster than subprocesses. Restricting `citeapp` to a declared surface (rather than reaching into arbitrary internals) keeps `refcite` free to evolve and makes the two code bases genuinely separable. Surfacing live progress into the GUI (A14) needs a hook the current stderr-only reporter does not expose, so a minimal additive change is accepted; it does not alter CLI behaviour. |
| A4 | **LLM access is OpenAI-compatible `/v1/chat/completions` over HTTP.** OpenAI is the first target; LM Studio / Ollama / proxies work via the same shape. | One client covers hosted OpenAI and local servers (LM Studio already runs on the author's machine for embeddings). Anthropic's native API is out of scope for v1 (reachable via an OpenAI-compatible proxy if needed). |
| A5 | **The model is asked for schema-constrained JSON via `response_format`, validated against the schema, with one automatic retry and a visible error path.** The `response_format` is sent as `json_schema`, falling back to `json_object` then plain text per server support. | LLM output varies *and so does server support for `response_format`*: the Stage A0 spike found LM Studio rejects `{"type":"json_object"}` (HTTP 400 — "must be 'json_schema' or 'text'") but honours a `json_schema` format, which current OpenAI models also accept. Schema-constrained JSON + validation + retry handles the common cases; a parse failure surfaces to the user rather than corrupting a claim, with the raw response available to a developer. |
| A6 | **Raw model JSON is hidden from the author; a developer toggle exposes it.** | Authors should never need to read JSON. Developers debugging variability need the raw payload. A collapsible/disclosable panel (off by default) serves both. |
| A7 | **The author never classifies claims or edits claim types / search queries.** The model does it. | App mode must match agent-mode effort exactly: in agent mode the author focuses only on which *sentences* to cite, not their taxonomy. Adding classification work would invalidate the comparison. |
| A8 | **In the statement phase, adding or dropping a proposed sentence is pure GUI; classification and `search_query` are produced for the whole confirmed set in one batch (LLM call 2), not per sentence.** | Splitting statement proposal (call 1) from claim generation (call 2) lets the author edit the set with zero model latency, then classify everything at once. Keeps author effort to "pick sentences" only (A7), and a manually-added sentence is classified by the same call 2 as the model-proposed ones. |
| A9 | **One working document per session, loaded read-only.** A session starts either from a **loaded file** (drag-drop / File-open) or from **pasted text** (A36). | `insert` matches sentences verbatim by line + text (DESIGN.md D15); a read-only document guarantees the "do not edit the report between search and insert" rule is met by construction. Multi-file reports are handled one chapter at a time, mirroring how an author works in agent mode. Pasted text is written once into a read-only scratch document so the same verbatim/line contract holds. |
| A10 | **Project root depends on the input mode. For a loaded file it is the folder containing that file (`references.md` and `citations/` live there). For a pasted session it is an ephemeral scratch folder (A37).** The cache and all app data live user-global under `~/.cite-assist/`. | The author who edits one document on disk wants the outputs beside it; that folder is the natural project root, and `load_config(repo_root=<doc folder>)` already supports it. A pasted session has no on-disk home, so its project root is a throwaway scratch folder instead (A37) and the author copies the results out of the result tabs (A38). No separate "working folder" step in either mode. |
| A11 | **Selection happens in the document pane itself.** A `tkinter` `Text` selection yields the exact text and its line range, which map straight onto the verbatim-sentence + line that `claims.json` needs. | Removes the awkward "open a second editor, copy, paste" dance. The GUI is the editor-of-record for the selection, and the line numbers come for free. |
| A21 | **File loading: a File-open dialog is the cross-platform baseline; drag-and-drop is a Windows-only, no-dependency enhancement** via `ctypes` (Win32 `DragAcceptFiles` + `WM_DROPFILES`). On macOS/Linux the app relies on File-open. **Pasted text (A36) is a third input path available on every platform.** | Plain stdlib `tkinter` has no OS file drag-and-drop on any platform; `tkinterdnd2` would add a dependency, violating the zero-dependency goal. Windows is the author's primary platform, and Win32 drop is reachable through `ctypes` alone. Other platforms degrade gracefully to the dialog (Lesson L4). Paste needs no OS integration and so works the same everywhere. |
| A12 | **Two-pane horizontal layout (document left, interaction right) with a top menu bar.** | Mirrors the familiar VS Code editor-plus-panel feel; lets the author switch between the highlighted document and the long (~7000-word) review without losing either. |
| A13 | **"Open in Zotero" per candidate via `zotero://select/library/items/<key>`**, plus the DOI/URL as a clickable link. | Each cached record stores its `zotero_key`. The `zotero://select` URI jumps straight to the item in the desktop app, fully covering "find this reference in Zotero" without the app building any reference-inspection UI. |
| A14 | **Cache/Zotero status is shown in-app with a "Sync now" button** that runs the existing backend sync with a progress bar. | "Replicate the effort level" argues against dropping the author to a terminal. The backend already reports live progress (DESIGN.md D19); the GUI surfaces it. |
| A15 | **A single configured main model is used for all three LLM calls.** | Simplest setup. A per-task model override can be added later without changing the contract. The main model must hold enough scientific knowledge to judge claim–source alignment (call 3), so app mode targets moderate models, not very small ones. |
| A16 | **App mode ships as its own top-level package, `citeapp`, with two launch paths: a thin `app.py` and `python -m citeapp`. `refcite` has no `app` subcommand and never imports `citeapp`.** | Keeping the app in its own package (not under `refcite/`) and giving it its own `-m` entry point makes the dependency run one way only (`citeapp → refcite`); `refcite` stays a standalone library that still runs with the `citeapp/` folder deleted. A `refcite app` subcommand would force `refcite` to import the app, inverting that dependency, so it is deliberately omitted (A31). |
| A17 | **All app data lives under `~/.cite-assist/`** (cache, settings, obfuscated token, lessons are repo-tracked); ephemeral paste sessions live under `~/.cite-assist/scratch/` (A37). Settings file: `~/.cite-assist/app.json`. | One place for everything the app owns; nothing app-specific is written into the repo or the author's project beyond the citation outputs. Paste sessions, which have no author-chosen folder, get a scratch home under the same root. |
| A18 | **The API token is obfuscated, not encrypted.** A random 32-byte key in `~/.cite-assist/.appkey` (perms `600`) XORs the token; the result is base64-stored in `app.json`. | Defeats *untargeted* malware grepping the disk for `sk-...` (the token never appears in plaintext). It does **not** defeat a targeted attacker who reads both files or the source. Real protection needs the OS keychain (a third-party dependency), which the zero-dependency goal rules out. Documented honestly (Lesson L3). |
| A19 | **Session slug and lifecycle are automatic; a name collision appends an incrementing suffix until unique.** The app calls the same `new-session` scaffolding, derives a slug from the date + report name, writes `claims.json`, and lets `insert` move the session to `done/`. | The author should not name sessions; that is harness bookkeeping agent mode also hides. `new-session` hard-errors on an existing folder, so running the same report twice in a day retries with `-2`, `-3`, ... rather than failing. |
| A20 | **Every model-returned sentence is verified verbatim against the document** before becoming a claim; a non-verbatim return is flagged, not silently inserted. | `insert` locates sentences by exact text. A model that paraphrases would produce a claim that can never be inserted; catching it at selection time is the honest failure point. |
| A22 | **The backend gains an optional progress callback** that long operations (`sync`, `build_index`, embedding) invoke alongside their existing stderr line. The GUI passes a callback that marshals updates to the status strip; the CLI passes none and behaves exactly as before. | Realises A3/A14 without scraping stderr or stdout. A small additive change, no behaviour change for agent mode. |
| A23 | **The GUI reads backend return values for results, never parses stdout or `review.md` markdown.** `insert_session`, `sync`, `check_connection`, `build_index` already return structured dicts; `run_search` is extended to also return the per-claim structured candidates it already computes (A32). | Stable and testable; avoids brittle text parsing. The candidate display data (APA in-text form, confidence, tier, extracts, code, record id) lives only in the human-facing `review.md` markdown otherwise, so the GUI consumes it from the search return instead of re-parsing the file. |
| A24 | **Settings are deliberately minimal: the main-model connection (base URL, model, token) is required; a search-backend toggle (BM25 vs embeddings) and, when embeddings is chosen, the embeddings server URL + model are the only extras.** Everything else stays in `cite-assist.toml`. | Keeps the GUI simple to build. These are the only settings a user must set before the app is usable: BM25 needs no server, embeddings needs one, and every run needs the main model. |
| A25 | **The claim `line` is a best-effort hint, not a contract.** `insert` locates a sentence by exact (whitespace-normalised) string match, scanning the whole file if the hinted line misses. | The Tk widget's line index can drift from the on-disk file (newline normalisation, wrapped Markdown); since matching is string-based, a roughly-right line is sufficient and avoids over-engineering line tracking. |
| A26 | **One backend operation runs at a time; the UI disables conflicting actions while a worker is busy** (no Sync during search, no re-select mid-search, no Insert before evaluation). | A single worker thread plus a simple busy state removes a class of race conditions. |
| A27 | **Claim codes `C1..Cn` are re-derived in document order after every add/drop, immediately before `claims.json` is written.** | Codes are positional (DESIGN.md D17/D27); editing the statement set must renumber so the codes the author sees match the search output. |
| A28 | **A non-compliant call 1/2 return (non-verbatim or malformed) is logged as a warning and retried once; a persistent failure surfaces to the author, who can reset the session and restart the selection.** | Weak models will sometimes paraphrase or break the JSON shape. An in-place retry helps, but a hard "reset and start over" path acknowledges that a too-weak model may never comply, so the author is never stuck (A5, A20). |
| A29 | **`OPENAI_API_KEY` (environment) takes precedence over the stored obfuscated token.** | Lets a user keep the secret off disk entirely; env wins so a configured environment overrides a stale stored value. |
| A30 | **Persisted highlighting is a single background-colour tag on the confirmed claim sentences that survives later operations; the prototyping fallback is plain inline `===== start =====` / `===== end =====` markers.** | Only basic, durable highlighting is needed; no gutter or per-claim margin codes while prototyping, which Tk `Text` does not support natively. |
| A31 | **`refcite` exposes a narrow, declared integration surface that `citeapp` is the only consumer of, re-exported from `refcite/__init__.py`.** The surface is: `load_config` (with overrides, A34); `zotero_sync.check_connection`; `zotero_sync.sync`; `store.load_records` / `store.records_by_id` (cache count + per-record `zotero_key`/DOI for "Open in Zotero", A13); a session-scaffolding helper `create_session` (A19); the claims data contract (`Claim`, `save_claims`, `claims_template`); `search.run_search` (returning structured candidates, A32); `insert.insert_session`; a verbatim-match helper (A33); and a `zotero_select_uri(record, cfg)` helper. `citeapp` imports nothing else from `refcite`. | Treating `refcite` as a local library with a published surface (rather than a merged code base) keeps the two halves genuinely separable: `refcite` can change its internals freely as long as the surface holds, and the app's coupling is visible and testable in one place. Some of these (e.g. `create_session`, `zotero_select_uri`, the verbatim helper) do not exist yet and will be added to `refcite`; that is an accepted, additive change. |
| A32 | **`run_search` is extended to return the per-claim structured candidates it already builds internally** (`[(Claim, status, [Candidate])]`), so the GUI renders the recommendations view from `Candidate` objects rather than parsing `review.md`. The candidate `code` (e.g. `C3a`) is the same handle used everywhere else. | `run_search` already computes this list before rendering `review.md`; returning it is additive and avoids the GUI re-parsing human-facing markdown. The codes line up with what call 3 returns and what `insert --select` consumes, so the linkage is identical to agent mode (A33 below clarifies the call-3/insert path). |
| A33 | **Linkage for call 3 and insertion reuses agent mode's mechanism unchanged: LLM call 3 reads `review.md` as text and returns candidate codes; `insert_session(select=[codes])` maps those codes back to records exactly as `refcite insert --select` does today** (via the codes/record markers in `review.md`, cross-referenced to `claims.json`). The GUI never parses `review.md` itself. | The author's question: the code\u2192record\u2192claim linkage already exists and is proven in agent mode, so app mode does not invent a parallel one. The GUI's *display* data comes from `run_search`'s structured return (A32); the *insert* path comes from the existing code-based selection; the LLM's *judgement* comes from reading `review.md` text \u2014 three concerns, each served by the mechanism already built for it. |
| A34 | **The verbatim check (A20) reuses `refcite`'s existing sentence matcher** \u2014 the same whitespace-run-equivalent comparison `insert` uses to locate a sentence (`_find_span`/`_match_at`), exposed as a declared helper (A31). A sentence "matches verbatim" iff that matcher locates it in the document. | The check must agree with how `insert` later finds the sentence; reusing the one matcher guarantees that a sentence which passes the verbatim check at selection time is the same one `insert` can locate, and avoids a second, subtly different normalisation drifting out of sync. |
| A35 | **`load_config` gains an optional `overrides` dict merged over the loaded TOML**, so the GUI's `app.json` choices (search backend, embeddings URL/model \u2014 A24) take effect without writing a `cite-assist.toml` into the author's report folder. The progress callback (A22) is a single signature `on_progress(label: str, current: int, total: int)` threaded through `sync`, `build_index`, and `run_search`. | The app's users will not have a `cite-assist.toml` beside their report; overrides let the GUI supply the few settings it owns at call time. One callback signature covers every long operation (A22 confirms this suffices). Both are additive and unused by the CLI. |
| A36 | **A session can be started from pasted text, not only a file.** **File \u2192 New from paste** opens a paste box; the pasted prose becomes the read-only working document (A9) of an ephemeral scratch session (A37). The existing file drag-drop / File-open workflow is retained unchanged as the alternative input mode. | The author often wants to vet a snippet without first saving it to disk. Paste removes the "save a `.md` first" step while leaving the on-disk workflow exactly as designed, so the two input modes coexist and everything downstream (selection, claims, search, insert) is identical. |
| A37 | **A pasted session gets an ephemeral scratch project root under `~/.cite-assist/scratch/<random>/`** holding the working document, `references.md`, `citations/`, and the session folder. It is throwaway: not the author's folder, and not expected to persist after the author has copied the results out (A38). | A pasted session has no author-chosen folder to act as the project root (A10), so the app mints one. A random per-session code avoids collisions and keeps sessions isolated. Because the output is read back from the result tabs (A38) rather than from a path the author will revisit, the folder need not be durable. |
| A38 | **After Insert, two read-only result tabs are shown in both input modes: "Cited text" (the working document with citations inserted) and "References" (`references.md`).** The author reads them in-app and copies their contents out; this is the primary way to retrieve a pasted session's results, since its scratch folder is ephemeral (A37). | A pasted session has no on-disk destination the author opens afterwards, so the results must be visible and copyable in the app. Showing the same two tabs for a loaded-file session too keeps the GUI uniform and lets the author confirm the inserted citations without leaving the app (the file on disk is also updated for that mode, A10). |

---

## 4. Architecture overview

App mode adds one new **top-level package (`citeapp`)** and a launcher alongside
the existing `refcite` package; `citeapp` depends on `refcite`, never the reverse.
The only changes to existing code are **additive and backward-compatible**: an
optional progress callback through the long-running backend operations (A22),
structured candidate results from `run_search` (A32), a `load_config` overrides
parameter (A35), a few small declared helpers (`create_session`,
`zotero_select_uri`, a verbatim-match helper — A31), and the fixed Zotero default
URL (D22). `refcite` never imports `citeapp`; the dependency runs one way only.
Agent mode and the CLI are otherwise untouched.

```
cite-assist/
  app.py                         # NEW thin launcher: imports citeapp and runs it
  refcite/                       # the backend — standalone library, no knowledge of citeapp
    __init__.py                  # re-exports the declared integration surface (A31)
    __main__.py                  # CLI dispatch — UNCHANGED (no `app` subcommand, A16)
    config.py                    # default Zotero URL 127.0.0.1 (D22); load_config(repo_root=..., overrides=...) (A35)
    progress.py                  # gains an optional on_progress callback hook (A22)
    search/                      # run_search also returns structured candidates (A32)
    claims.py  insert.py  zotero_sync.py  store.py  apa.py  ...   # declared helpers added (A31); behaviour unchanged
  citeapp/                       # NEW — the desktop app (tkinter); imports refcite one-way only
    __init__.py                  # main() entry point
    __main__.py                  # `python -m citeapp`
    gui.py                       # the two-pane window, menu, panels (tkinter only)
    llm.py                       # OpenAI-compatible chat client over urllib; JSON + retry (A4, A5)
    tasks.py                     # the three LLM tasks: propose_statements, generate_claims, evaluate_review
    settings.py                  # ~/.cite-assist/app.json read/write; token obfuscation (A17, A18)
    orchestrator.py              # the Python loop: select -> claims -> search -> evaluate -> insert; mints scratch root for paste sessions (A37)
  docs/
    LESSONS.md                   # NEW running register for the agent-vs-app comparison (A1 motivation)
```

`citeapp` calls into the backend **only** through `refcite`'s declared integration
surface (A31), re-exported from `refcite/__init__.py`. Results are read from the
**return values** those functions produce, never by parsing stdout or `review.md`
(A23); long operations accept an optional **progress callback** (A22):

```python
from refcite import (
    load_config,            # (repo_root=..., overrides=...) -> Config            (A35)
    check_connection,       # cache/Zotero status for the status strip            (A14)
    sync,                   # update cache; on_progress=...                        (A22)
    load_records,           # cache count + per-record zotero_key/DOI             (A13)
    create_session,         # scaffold active/<date>_<slug>/ with collision suffix (A19)
    Claim, save_claims, claims_template,   # claims.json data contract
    run_search,             # sync + reindex + rank -> review.md + candidates      (A32)
    insert_session,         # write citations; select=[codes]; session -> done/
    sentence_matches,       # verbatim-match helper shared with insert             (A33/A34)
    zotero_select_uri,      # build zotero://select URI from a record + config     (A13)
)
```

Control flow (Python owns every arrow; only the three starred steps call the LLM):

```
drop/pick file ─OR─ paste text (A36) ─► set project root: doc folder, or ephemeral scratch (A10/A37) ─► load_config(repo_root=root) ─► show cache status (A14)
   │
   ▼
author selects text in document pane (A11)
   │
   ▼ *LLM call 1: statement proposal (tasks.propose_statements)*  →  verbatim check (A20)
present proposed sentences (checklist right pane; persistent highlight in doc A30)
   │   author drops/adds sentences — pure GUI (A8)
   ▼ author confirms set
   ▼ *LLM call 2: claim generation (tasks.generate_claims)*  →  verbatim check (A33/A34) + renumber codes (A27)
create_session scaffold (A19) + write work/claims.json (Claim, save_claims)
   │
   ▼ run_search(cfg, session)            ── pure Python, no LLM ──►  review.md + structured candidates (A32)
   │
   ▼ *LLM call 3: candidate evaluation (tasks.evaluate_review)*  reads the whole review.md text
present per-claim recommendations (rendered from A32 candidates; checkboxes pre-ticked, rationale, extract, Open-in-Zotero A13)
   │   author adjusts selection; may open review.md to dig deeper
   ▼ author clicks Insert
insert_session(cfg, session, select=[chosen codes])   ── pure Python ──►  document edited; refs appended; session -> done/
   │
   ▼ reload document pane + open the Cited-text and References result tabs (A38) so the author can copy results out
```

---

## 5. The GUI (tkinter, two-pane)

**Window:** a top **menu bar** (File: open / **new from paste** / recent; Settings:
API endpoint, model, token; Help/About; a hidden Developer toggle for raw JSON —
A6) over a horizontal `PanedWindow` whose left side is a tabbed `ttk.Notebook`
(Document, plus the post-insert result tabs — A38) and whose right side is the
interaction panel.

- **Left pane — a tabbed `ttk.Notebook`.** Its first tab, **Document**, is a
  read-only `Text` widget holding the working document. A session starts one of two
  ways (A36): **(a)** loading a `.md` file via a **File-open dialog on every
  platform** or **drag-and-drop on Windows** (A21, via `ctypes`; on macOS/Linux the
  window shows a "drag-drop unavailable — use File → Open" hint); or **(b)** **File
  → New from paste**, which opens a small paste box and writes the pasted prose into
  an ephemeral scratch document (A37). Either way the Document tab is read-only (A9)
  so the verbatim/line contract holds. The author's current selection is
  highlighted; once statements are confirmed, each claim sentence keeps a
  **persistent background-colour tag** that survives later operations (A30; the
  prototyping fallback is plain inline `===== start =====` / `===== end =====`
  markers). After **Insert**, two further read-only tabs appear — **Cited text**
  (the working document with citations inserted) and **References**
  (`references.md`) — identical in both input modes (A38); the author copies their
  contents out (for a pasted session there is no on-disk destination, A37).
- **Right pane — interaction.** A scrollable panel whose content changes by phase:
  1. **Status strip** (top, always visible): cache record count + Zotero
     reachability + a **Sync now** button (A14), driven by backend return dicts
     (A23) and a live progress callback (A22).
  2. **Statements view:** the proposed citable sentences (LLM call 1) as a
     checklist (keep/drop), plus an "Add selected sentence" button. Add and drop
     are **pure GUI** (A8) — no model call — because classification and the search
     query are produced for the whole confirmed set by LLM call 2. A **Find
     citations** button confirms the set, runs call 2, writes `claims.json`, and
     kicks off search. A **Reset** action discards the proposal and lets the
     author start the selection over (A28).
  3. **Recommendations view:** one block per claim — the sentence in context, the
     model's recommended candidate(s) pre-ticked, each candidate showing its APA
     in-text form, confidence/tier, the supporting extract, a one-line model
     rationale, an **Open in Zotero** button and a DOI/URL link (A13). A button to
     **Open review.md** (whichever is simplest: the system editor, or a read-only
     tab inside the app) for deeper reading. An **Insert** button applies the
     ticked codes.

The author switches freely between reading the highlighted document (left) and the
recommendations (right), as in VS Code (A12).

**Threading.** LLM calls, `run_search`, `sync`, and `insert` run on a worker thread
so the Tk event loop stays responsive; results are marshalled back to the UI thread
via a queue. Only **one** backend operation runs at a time and conflicting actions
are disabled while a worker is busy (A26). Long backend operations report progress
through the optional callback (A22), which the worker forwards to the status strip;
final counts come from the operation's return dict (A23).

---

## 6. LLM client and structured output (A4, A5)

`llm.py` — a minimal OpenAI-compatible chat client over `urllib.request`:

- `POST {base_url}/chat/completions` with `Authorization: Bearer <token>`,
  `model`, `messages`, and a `response_format` that constrains the reply to the
  call's JSON schema. The client sends `{"type":"json_schema", ...}` and, on a
  400 that names an unsupported format, **falls back** to
  `{"type":"json_object"}` and then to no `response_format` (plain text) —
  always followed by the A5 schema validation + single retry. This is required
  because servers disagree: the Stage A0 spike found LM Studio rejects
  `json_object` but accepts `json_schema`, while older proxies accept neither.
- Returns the assistant message content. Network/HTTP errors raise a typed
  exception the GUI shows as a friendly message ("could not reach the model API
  at <url>").

`tasks.py` — the three prompts, their JSON schemas, and validation:

- **`propose_statements(selection, context) -> list[str]` (LLM call 1).** The
  system prompt states the golden rules (never fabricate; copy sentences
  **verbatim**; propose only sentences that plausibly need a citation). The
  expected JSON is just the candidate sentences, with no taxonomy yet:

  ```json
  { "sentences": ["verbatim sentence copied from the selection", "..."] }
  ```

  After parsing, the app **verifies each sentence appears verbatim** in the
  selection (A20) using `refcite`'s own sentence matcher — the same
  whitespace-run-equivalent comparison `insert` uses to locate a sentence,
  exposed as a declared helper (A33/A34) so a sentence that passes here is one
  `insert` can later find. A non-verbatim or malformed return is logged and
  retried once, then surfaced for the author to **Reset** (A28). The author then
  drops or adds sentences in the GUI (A8) before confirming.

- **`generate_claims(sentences, document) -> list[Claim]` (LLM call 2).** Given
  the author-confirmed sentences, the model classifies each
  (`background | method | own-project | common-knowledge`) and writes a concise
  conceptual `search_query`, returning the per-claim metadata:

  ```json
  {
    "claims": [
      {
        "sentence": "verbatim sentence (must match the confirmed one)",
        "claim_type": "background|method|own-project|common-knowledge",
        "search_query": "concise conceptual terms",
        "existing_citations": ["(Smith, 2019)"]
      }
    ]
  }
  ```

  Each returned `sentence` is re-checked verbatim with the same shared matcher
  (A33/A34); the app then assigns the line number from the document (a best-effort
  hint only — A25), fills `context_before` / `context_after` from the surrounding
  text, re-derives the positional `C`-codes in document order (A27), and writes
  `work/claims.json`.

- **`evaluate_review(review_md) -> dict[claim_code, {keep: [codes], why: str}]`
  (LLM call 3).** The system prompt gives the same honesty rules and asks the
  model to recommend the strongest candidate(s) per claim from the supplied
  `review.md`, or none if nothing is convincing (mirroring `NEEDS SOURCE`). The
  model is given the **whole `review.md` in one call** — every claim and every
  candidate — because choosing between candidates is a relative ranking that needs
  them all in view (A15), the same context the agent has in agent mode. It is
  **not** given the full cached record text, because letting it request and read
  more material would turn app mode into an agentic harness (Lesson L1). Expected
  JSON:

  ```json
  {
    "recommendations": [
      { "claim": "C3", "keep": ["C3a"], "why": "Full-text match states the 1,023 figure directly." },
      { "claim": "C4", "keep": [], "why": "Only weak abstract overlap; leave uncited." }
    ]
  }
  ```

**Reliability:** each call parses JSON; on `JSONDecodeError` or schema mismatch it
retries once with a terse "return valid JSON only" reminder; a second failure
raises to the GUI, which shows a friendly error and (for developers) the raw text
(A5, A6). For calls 1 and 2 the author can additionally **Reset** and restart the
selection when a too-weak model never complies (A28). Unknown candidate codes in
`keep` are ignored with a warning rather than passed to `insert` (which would
hard-error on an unknown code).

---

## 7. Settings, token storage, and security (A17, A18)

`settings.py` manages `~/.cite-assist/app.json`:

```json
{
  "api_base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "token_obfuscated": "<base64 of token XOR appkey>",
  "search_backend": "bm25",
  "embeddings_url": "http://127.0.0.1:1234",
  "embeddings_model": "text-embedding-embeddinggemma-300m-qat"
}
```

Settings are deliberately minimal (A24): the **main-model connection**
(`api_base_url`, `model`, token) is required; a **search-backend toggle**
(`bm25` needs no server, `embeddings` does); and, only when `embeddings` is
selected, the **embeddings server URL and model**. Everything else (own
collections, extract sizes, fragment limits, ...) stays in `cite-assist.toml`.
The chosen backend and embeddings connection are passed to `load_config` as an
**`overrides` dict** (A35), so the GUI's choice overrides the toml default
without the app having to write a `cite-assist.toml` into the author's report
folder (which will usually not contain one).

- The **token** is never written in plaintext. On first save, a random 32-byte key
  is generated and written to `~/.cite-assist/.appkey` with `0o600` permissions;
  the token bytes are XORed with the (repeated) key and base64-stored in
  `app.json`. Reading reverses this.
- **`OPENAI_API_KEY` (environment) takes precedence over the stored token** (A29):
  if the env var is set, it is used and the stored value is ignored, so a user can
  keep the secret off disk entirely.
- **Threat model (explicit):** this stops *untargeted* malware that greps the disk
  for token-shaped strings — the token never appears in plaintext anywhere. It does
  **not** stop an attacker who can read both `app.json` and `.appkey`, or who reads
  this source. That is the accepted limit (A18, Lesson L3). Users who need real
  secret protection should set the token via the environment variable the app
  honours (`OPENAI_API_KEY`, A29), so the secret never touches disk via this app.

The base URL, model, and search-backend choice persist in `app.json` so the author
configures them once. A **Settings** dialog in the menu edits them.

---

## 8. Setup and run

Intended to be as low-friction as possible:

1. Clone/download this repository.
2. Ensure a Python 3.11+ with `tkinter` (python.org installers include it; on some
   Linux distros `sudo apt install python3-tk`).
3. From `archive/agent-app/`: `python app.py` (or `python -m citeapp`).
4. First run: open **Settings**, paste the API base URL, model name, and token
   (stored obfuscated, once — A18), and choose the search backend — **BM25**
   (no server) or **embeddings** (set the embeddings server URL + model) (A24).
5. Drag a report `.md` file onto the window (or File → Open), **or File → New from
   paste** to start an ephemeral scratch session from copied text (A36/A37). If the
   cache is empty or Zotero is unreachable, the status strip says so and offers
   **Sync now**.
6. Select prose → **Find citations** → confirm statements → review recommendations
   → **Insert**.
7. Read the **Cited text** and **References** result tabs (A38). For a pasted
   session, copy their contents out to wherever you need them — the scratch folder
   is ephemeral (A37). For a loaded file, the document on disk is also updated and
   `references.md` is written beside it (A10).

The author's project (the report folder, or the scratch folder for a pasted
session) needs the same prerequisites as agent mode: a synced `~/.cite-assist`
cache and, for embeddings search, a reachable model server (LM Studio / Ollama).
For a loaded file, `references.md` and `citations/` are created in the report's
folder (A10); for a pasted session they live in the ephemeral scratch folder (A37).

---

## 9. Staging

**Stage A0 — Technical-risk spikes (no app code).**
Before building the shell, validate the riskiest backend assumptions with the
throwaway scripts in [`spikes/`](./spikes/) (outcomes recorded in
[`spikes/RESULTS.md`](./spikes/RESULTS.md)): the **declared integration surface**
(A31) imports and runs in-process; `run_search` returns usable structured
candidates (A32); the **chat client + `response_format`** behaviour across
servers (A4/A5); the **`zotero://select` URI** built from
`library_type`/`library_id` actually selects the item in the desktop app (A13);
and the shared **verbatim matcher** (A33/A34) agrees with what `insert` later
locates. Resolving these first de-risks Stages A1–A3. As of 2026-06-20 the
offline spikes plus the LM Studio chat/embeddings spikes pass; the one finding
with design impact is the `json_schema` requirement folded into A5.

**Stage A1 — Walking skeleton.**
`app.py` launcher + `python -m citeapp` (no `refcite app` subcommand, A16);
two-pane window with the left **`ttk.Notebook`** (Document tab); file open/drag-drop
**and File → New from paste creating an ephemeral scratch session (A36/A37)**;
document tab read-only; Settings dialog
with obfuscated token, backend toggle and embeddings connection (A24, passed as
`load_config` overrides — A35); status strip wired to `load_records` /
`check_connection`. Add the additive backend progress callback (A22) and confirm a
**Sync now** run drives it. No LLM yet — prove the shell and backend wiring,
including the per-session metrics log (see research notes).

**Stage A2 — Statement selection + claim generation (LLM calls 1 & 2).**
`llm.py` + `tasks.propose_statements` (call 1) with verbatim verification via the
shared matcher (A33/A34) and the pure-GUI add/drop checklist (A8); confirm →
`tasks.generate_claims` (call 2) → `create_session` (A19) + `claims.json` with
renumbered codes (A27); run `run_search` → `review.md` + structured candidates
(A32). Right pane shows raw candidates (no evaluation yet) so the path to `insert`
works end to end with a human reading `review.md`. Include the **Reset** path for
non-compliant returns (A28).

**Stage A3 — Candidate evaluation (LLM call 3) + insert.**
`tasks.evaluate_review` over the whole `review.md` text; recommendations view
rendered from the A32 candidates with pre-ticked checkboxes, rationale,
Open-in-Zotero, DOI links; **Insert** via `insert_session(select=...)`; reload the
Document tab and open the **Cited text** and **References** result tabs (A38).

**Stage A4 — Polish.**
Developer raw-JSON panel; recent-files; single-model-with-optional-override hook;
busy-state hardening (A26); cross-platform checks (drag-drop and `zotero://`
behaviour on macOS/Linux).

---

## 10. Open items to confirm during build

- **Session-scaffold timing vs. Reset (A28).** `create_session` (A19) is called
  only **after** call 2 succeeds, so a **Reset** before that point is pure GUI
  state with no folder to clean up; confirm this ordering survives the worker/busy
  model (A26).
- **`load_config` with no `cite-assist.toml` in the report folder.** Confirm the
  defaults plus the `overrides` dict (A35) yield a sane config (cache under
  `~/.cite-assist`, sessions + `references.md` under the report folder, A10) when
  the author's folder has no toml.
- **Scratch session lifecycle (A37).** Confirm a pasted session's ephemeral root
  under `~/.cite-assist/scratch/<random>/` is created before `create_session`/
  search run, that `load_config(repo_root=<scratch>)` with overrides (A35) yields a
  sane config, and decide when (if ever) the app prunes old scratch folders — the
  author is expected to copy results out via the result tabs (A38) rather than
  revisit the folder.

Resolved since the first draft:
- **`zotero://select` deep link is validated** (A13): the URI built from
  `library_type`/`library_id` (`zotero://select/library/items/<key>` for a user
  library) made the running Zotero desktop jump to the correct item. Build the
  URI from config rather than hard-coding `/library/` so group libraries work
  (`spikes/03_zotero_select.py`).
- **Windows drag-and-drop is validated** (A12/A21): the `ctypes`
  `DragAcceptFiles` + `WM_DROPFILES` subclass works and decodes Unicode/spaced
  paths, but the implementation **must** (1) give `SetWindowLongPtrW` /
  `CallWindowProcW` explicit `argtypes`/`restype` (`c_void_p` / `c_ssize_t`) or
  the 64-bit WNDPROC pointer overflows, and (2) accept files on
  `GetParent(winfo_id()) or winfo_id()` because `GetParent` returned `0` on the
  tested Tk build (applying `DragAcceptFiles` to the wrong window gives a silent
  "no-drop" cursor). Add `ChangeWindowMessageFilterEx` defensively for the
  elevated-process case. See [`spikes/RESULTS.md`](./spikes/RESULTS.md), spike 08.
- **`response_format` portability** is settled by the Stage A0 spike: send
  `json_schema`, fall back to `json_object` then plain text, always with the A5
  validate-and-retry path. LM Studio (`google/gemma-4-e4b`) needs `json_schema`;
  OpenAI accepts it too (A4/A5, see [`spikes/RESULTS.md`](./spikes/RESULTS.md)).
- Sentence-to-line mapping is no longer a concern — the `line` field is a
  best-effort hint and `insert` matches by exact string (A25).
- The candidate display / call-3 / insert linkage is settled: the GUI renders from
  `run_search`'s structured candidates (A32), call 3 reads `review.md` text and
  returns codes, and `insert_session(select=...)` maps codes back exactly as agent
  mode does (A33) — no markdown parsing in the GUI.
- Verbatim normalisation reuses `refcite`'s existing sentence matcher (A33/A34).
- The progress-callback signature is fixed at `on_progress(label, current, total)`
  and covers `sync`, `build_index`, and `run_search` (A35).
- Code-base separation is settled: app mode is the top-level `citeapp` package
  consuming `refcite`'s declared surface only (A16, A31); `refcite` never imports
  `citeapp`.
