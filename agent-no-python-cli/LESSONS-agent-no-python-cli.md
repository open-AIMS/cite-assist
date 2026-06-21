# Lessons — No-Python-CLI Agent Mode

A running, reportable log of what we tried while developing the skill-only
implementation, and the keep/drop decision each tactic earned. Raw spike detail
is in [`spikes/RESULTS.md`](spikes/RESULTS.md); intent is in
[`DESIGN-agent-no-python-cli.md`](DESIGN-agent-no-python-cli.md).

Format per lesson: **what we learned → decision → why it matters for the skill /
the paper.**

---

## L1 — Zotero needs no cache to be searchable; it already is a search server
**Learned.** The local API answers `qmode=everything` full-text queries
server-side and returns clean extracted text on demand. The entire raison d'être
of the `refcite` cache + BM25/embeddings index (agent mode) is, for retrieval
purposes, *optional* when Zotero itself is running.
**Decision.** KEEP the API as the primary route; build **no index**.
**Why it matters.** The headline finding for the paper: a large part of what we
engineered in agent mode (sync, cache, fragment, index) is replaceable by "ask
Zotero directly." The CLI's real value is therefore *not* search — it is
determinism, offline use, and ranking control. That reframes what the CLI buys
you.

## L2 — Full-text retrieval is a two-hop walk, and the agent will get it wrong unless told
**Learned.** `/items/<key>/fulltext` on a *top-level* key returns nothing; text
lives on the **attachment** child. Items can also have only notes (no PDF).
**Decision.** KEEP an explicit, spelled-out parent→`/children`→attachment→
`/fulltext` recipe in the skill (a copy-paste snippet), rather than trusting the
agent to rediscover it each time.
**Why it matters.** A skill-only approach lives or dies on *removing
rediscovery cost*. Anything the agent has to re-derive every session is a
reliability leak; the skill must hand it the exact path.

## L3 — Read-only disk access works while Zotero is open (`immutable=1`)
**Learned.** `sqlite3.connect("file:...zotero.sqlite?immutable=1", uri=True)`
opens without a lock error mid-session. So "close Zotero to read the DB" is *not*
required.
**Decision.** KEEP disk access as an optional augmentation, chiefly for
**collections** (own-project biasing) which the API exposes awkwardly. Do not
make it the primary route.
**Why it matters.** Lowers the risk of the disk route; but the DB's full text is
tokenized-only, so disk never replaces the API for readable extracts.

## L4 — Full-text coverage is partial (62 / 261); most items are abstract-only
**Learned.** Only 62 items have an extracted-text cache; the rest are
metadata/abstract. This mirrors agent mode's `full` vs `abstract`/`meta` tiers.
**Decision.** KEEP a tier-aware expectation in the skill: search full text where
it exists, fall back to title+abstract everywhere else, and label the difference
for the author.
**Why it matters.** Sets honest expectations — neither mode can full-text-match a
source Zotero never indexed; this is a library property, not an implementation
gap.

## L5 — `format=keys` and response headers make the API scriptable and cheap
**Learned.** `format=keys` returns a bare newline-delimited key list; headers
give `Total-Results` and version without paging.
**Decision.** KEEP `format=keys` for the search step and headers for the
reachability/size probe — minimises tokens pulled into context.
**Why it matters.** Context budget is the main scaling limit without a RAG; lean
primitives directly address risk R2.

---

## L6 — The API `q` search is an AND word-filter, not a relevance ranker (decisive)
**Learned.** `q=<terms>&qmode=everything` returns only items containing **every**
query word; a natural-language query of a whole claim returns **0 hits**. Narrow
queries (one distinctive term, or a tight pair) return relevant candidates;
absent terms (e.g. `macrotidal`) return nothing.
**Decision.** KEEP a "narrow-query union" retrieval pattern in the skill: per
claim, derive a few **distinctive keyword probes** (proper nouns, dataset names,
jargon), run each separately, union the keys, then have the **agent read and
rank**. DROP any instinct to pass the sentence or a long conceptual phrase as one
query.
**Why it matters.** This is the central design pivot. Unlike BM25/embeddings
(which the other modes own), Zotero's search does no partial-overlap ranking, so
the **agent must supply the ranking** by reading metadata/abstracts. It reframes
the skill's job as *probe well + judge well*, and it is the main reliability
hazard — a poorly chosen probe silently returns nothing.

## L7 — Relevance ranking is the agent's job; the API only supplies a candidate pool
**Learned.** When narrow probes do hit, the results are on-topic but unordered by
claim relevance. The intelligence that the RAG index provided in agent mode now
has nowhere to live except the agent's reading.
**Decision.** KEEP an explicit "read abstract first, rank by judgement, open full
text only when borderline" loop in the skill.
**Why it matters.** Confirms the paper's framing: the CLI's index buys *ranking
quality and recall*, not raw access. Skill-only trades those for agent judgement
— testable as a quality delta.

## L8 — A single enrich recipe handles metadata + abstract + full text + tiering
**Learned.** One small helper (`zenrich.py`) reliably returns citation metadata,
abstract, and — via the two-hop walk — full-text snippets, and degrades cleanly
to abstract tier when there is no PDF (46k chars for Morgan 2016; 0 children for
the Seamap dataset).
**Decision.** KEEP an explicit enrich recipe in the skill (the agent writes this
throwaway script, or runs the documented `curl` walk).
**Why it matters.** Removes the most error-prone rediscovery (the parent→child→
fulltext hop, L2) by giving the agent a proven, copy-pasteable procedure.

## L9 — All APA fields are in the API `data` block; agent formats APA directly
**Learned.** `journalArticle`/`report`/`webpage`/`dataset` each expose the fields
APA needs (`publicationTitle`/`volume`/`pages`/`DOI`; `institution`/`place`;
`publisher`/`url`; `repository`/`versionNumber`). Creators are
`lastName/firstName` **or** a single `name` for institutional authors.
**Decision.** KEEP an explicit APA template per item type in the skill, including
an institutional-author case. DROP reliance on any formatter — there is none.
**Why it matters.** Without `apa.py`, formatting consistency is wholly the
skill's responsibility; spelled-out templates are the only guard against
inconsistent or malformed references (risk R5).

## L10 — Inline Python one-liners with nested quotes are fragile; prefer throwaway script files
**Learned.** A `python -c '...'` with escaped `\"` inside a Git Bash
single-quoted string failed with a `SyntaxError`; writing the same logic to a
`.py` file ran first time.
**Decision.** KEEP guidance in the skill to **write a throwaway `.py`/script file
and run it** rather than composing quote-heavy one-liners, especially on Windows
Git Bash.
**Why it matters.** A skill-only build leans heavily on ad-hoc scripting;
flagging this avoids a recurring, time-wasting failure mode for the agent.

## Provisional design conclusions (pre-SKILL)

- The skill's retrieval core is: **API `qmode=everything` keyword search →
  metadata-first read → full-text extract only when borderline → agent ranks.**
- The skill must **spell out the fiddly bits** (two-hop full text, immutable DB
  URI, APA templates) so the agent never rediscovers them — the single biggest
  reliability lever for a skill-only build.
- The CLI's genuine differentiators over skill-only are **offline operation,
  deterministic ranking, and managed insert/backups** — *not* search itself.

## Still open
- Whether agent re-ranking beats Zotero's `everything` ordering (S7).
- Real token cost per session at 261 items (S8).
- Keyword-only recall gap vs embeddings (S10) — expected to be the main quality
  cost of dropping the RAG.
