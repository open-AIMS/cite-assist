# Live Zotero Python CLI

This version extends the [natural-language skill](../agent-no-code/SKILL.md),
which was derived from the [earlier live-API skill](../archive/agent-no-python-cli/SKILL.md).
It replaces repeated agent-written Zotero access code with read-only tools,
without the whole-library cache of the [archived RAG CLI](../archive/agent-cli/README.md).
It bundles repeatable, read-only tools
for Zotero's local API. The agent supplies keyword strategy, evaluates evidence,
obtains author approval, formats APA and edits documents. The CLI never edits the
report or mutates Zotero.

Start with [SKILL.md](./SKILL.md). Code is in [scripts/zotero.py](./scripts/zotero.py);
tests are in [tests/test_zotero.py](./tests/test_zotero.py).

## Setup

Requires Python 3.10+ and Zotero desktop with its local API enabled. No third-party
runtime dependencies, credentials, installation or embeddings server are needed.
It supports only the personal library at `/api/users/0`, using IPv4 loopback.
In this repository activate `cite-assist-dev` before running Python in Git Bash.

The preferred use is to clone this repository, add this complete folder to the
report project's VS Code workspace with **Add Folder to Workspace**, and explicitly
attach `SKILL.md` to the prompt. See the [shared usage guide](../README.md#using-the-phase-2-skills).
No personal skill installation or automatic activation is required. Resolve the
script relative to this folder, but run it from the author's report project root
so temporary files stay there. If using a harness's normal skill installation
instead, copy the complete folder and keep the name `agent-python-cli` to match
the skill frontmatter.

Tested version: `1.0.0`, recorded in `SKILL.md` under `metadata.version`. The
version covers the instructions and bundled script. Increase it when either
changes and record the new version in subsequent evaluation notes.

## Commands

```bash
python agent-python-cli/scripts/zotero.py --help
python agent-python-cli/scripts/zotero.py --format agent init --check --query "reef mapping" --query "turbid"
```

After statement confirmation, the combined `init` command checks the live API,
creates the working folder and runs the first batch of searches in one invocation.
A search implies `--check`; `init --query ...` also performs the check. It accepts
the same `--query` or `--requests FILE`, `--mode`, `--limit`, `--start` and
`--abstract-chars` options as `search`. Use inline queries to avoid needing a
working request file before the folder exists. An existing requests file can
instead supply claim-specific probes.

Plain `init` still creates a folder without contacting Zotero. `init --check`
checks and creates a folder without searching; standalone `status` remains
available for diagnostics. A failed initial check creates no folder. A combined
search returns `work_dir`, nested `status`, the normal search fields and API/reuse
counts. Partial search failures return non-zero while preserving successful
results and the folder for retries or cleanup. Search metadata is saved for
subsequent fetches, just as with standalone `search`.

Assign the absolute `work_dir` returned by `init` to `WORK`. Subsequent examples
assume the repository root; installed skills use their actual script path.

```bash
python agent-python-cli/scripts/zotero.py --work-dir "$WORK" search --query "reef mapping" --query "turbid"
python agent-python-cli/scripts/zotero.py --work-dir "$WORK" fetch ABCD1234 --terms "turbid" "mapping"
python agent-python-cli/scripts/zotero.py --work-dir "$WORK" passages PDF12345 --terms "limitations" --count 8
python agent-python-cli/scripts/zotero.py --work-dir "$WORK" passages PDF12345 --start 5000 --window 2000
python agent-python-cli/scripts/zotero.py --work-dir "$WORK" cleanup
```

Keys here are examples; use real keys returned by searches. Default output is JSON;
use global `--format agent` for bounded readable research output.
Exit status 1 means an error, including partial batch failures; successful parts
are still returned. `status` is always live, never served from session files.

| Command | Purpose and controls |
| --- | --- |
| `init` | Unique `.cite-assist-work-*` folder; optional `--check` and first batched search via `--query` or `--requests`. Returns absolute path and any check/search results. |
| `status` | Live reachability, top-level result count, library version. |
| `search` | Repeat `--query`, or `--requests FILE` for `[{"claim_id":"C1","queries":["reef","turbid"]}]`. Returns claim/probe provenance and unique metadata summaries. |
| `fetch` | Multiple parent keys, or `--requests FILE` for `[{"key":"ABCD1234","terms":["reef","accuracy"]}]`. Returns full bibliographic `data`, bounded abstract and optional PDF passages. |
| `passages` | Attachment key with `--terms WORD_OR_PHRASE ...`, or `--start OFFSET` for an exact window in saved text. No network requests. |
| `cleanup` | Validate the tool's marker and path, then remove the entire working folder, including request files and backups. |

Global `--work-dir`, `--refresh`, `--format` and `--output-budget` go **before** the command. `--refresh` bypasses
disk response reuse for that invocation; duplicate requests within it still reuse
memory. Refresh affected queries/items if Zotero changes. A new session is the
simplest way to discard all old results. Nothing is reused across working folders.

Search defaults: `--mode everything`, `--limit 20`, `--start 0`,
`--abstract-chars 2000`. Zotero's page limit is at most 100; use returned
`next_start` for more results. Offsets count raw API results, including filtered
notes, so do not calculate offsets from visible candidate counts. Pagination is
explicit rather than silently exporting all matching records.

Fetch defaults: `--abstract-chars 4000`, `--count 5`, `--window 900`. Without
`--terms` or `--fulltext`, it retrieves only metadata. With terms, it retrieves
all PDF children's indexed text, saves it locally, and prints selected passages.
`--fulltext` saves text without printing entire documents. Missing indexing is
recorded as `not_indexed`; other failures are explicit errors. Attachment-list
pagination is handled internally. Notes and non-PDF attachments are not read.

## Passage Selection

The extractor searches the entire requested PDF text for several case-insensitive
literal words/phrases. Phrase whitespace may span lines; word boundaries avoid
partial-word matches. Each hit proposes a character window. Nearby windows merge
up to twice `--window`. Candidates sort by distinct term coverage, then hit count,
then document position. Selected windows do not overlap.

Output includes exact text and zero-based character offsets (`end` exclusive),
matched terms, hit counts, candidate count and `selection_limited`. Passage
selection limits are per attachment. Agent-format terminal output has a separate
total byte budget across the entire command, so batched output remains bounded.
Increasing `--count`/`--window`, changing terms, or requesting an exact window
needs no new API calls once text is saved. The full text remains available at the
returned `text_path` for selective reading with ordinary file tools.

This is deliberately **not semantic retrieval or a scientific relevance score**.
References sections and repeated keywords can rank highly. Extraction artefacts,
hyphenation, inflections and synonyms can cause misses. The agent must vary terms,
inspect context and judge evidence. There is no automatic stopping threshold or
claim-support confidence. Offsets are not PDF page numbers.

## Agent Output and Saved Results

```bash
python agent-python-cli/scripts/zotero.py --format agent --output-budget 12000 --work-dir "$WORK" fetch ABCD1234 EFGH5678 --terms "glint" "noise"
```

`--format agent` renders source key, author/date/title/type/DOI once, matched
claim/probe provenance, probe totals and next offsets, evidence tier, attachment
statuses and passages. Full metadata is in the saved files rather than repeated
in the terminal. Abstract previews are at most 400 characters. Output is ASCII
escaped for terminal compatibility; saved UTF-8 evidence files preserve original
passage text and offsets.

`--output-budget` defaults to 12,000 bytes, with a minimum of 2,048. It applies
only to agent format, including the header, footer and newlines. Whole blocks are
omitted from the end when the budget is reached; passages are never silently
shortened. The header retains result paths and error counts; the footer reports
`output_limited`, `omitted` counts by block type (including items, errors and
passages), and `abstract_previews`. API errors are displayed before other blocks.
If error blocks do not fit, their omission counts remain visible and their full
details remain in the saved result. Output omission does not change exit status.

Session commands save results in either output mode. Each invocation creates a
new numbered folder, for example:

```text
results/0001-init/result.json
results/0001-init/result.txt
results/0001-init/ABCD1234.txt
results/0002-fetch/result.json
results/0002-fetch/result.txt
results/0002-fetch/ABCD1234.txt
```

`result.json` preserves the full command result and exposes `result_path`,
`readable_path` and `source_paths`. `result.txt` is an unbounded readable rendering
and source-file manifest. Each `KEY.txt` contains the source bibliography,
returned abstract, all selected passages and, for fetches, full bibliographic
metadata. Read these files using file-reading tools, not another CLI invocation
or a custom formatter. Earlier results are not overwritten, and saving makes no
additional API requests. JSON output remains structurally compatible, with these
new path fields added.

"Full command result" is not a whole-library export or a whole-PDF terminal dump.
Requested abstract and passage-selection limits still apply to saved results.
`abstract_truncated` and `selection_limited` describe retrieval limits, distinct
from terminal `output_limited`. Complete requested API responses remain in
`responses/`, and complete extracted PDF text remains in `texts/`.

Standalone `status`, errors before a valid session exists, and `cleanup` do not
save results. Cleanup removes result files along with the rest of the session.
Reading saved results requires no network requests or formatting scripts.

## Temporary Reuse

Only responses requested during the session are saved under `responses/`.
Repeated queries share one response, candidate keys are deduplicated, and search
metadata seeds later item retrieval. Full-text and attachment responses, including
unindexed status, are reused. Complete extracted PDF text is saved under `texts/`.
There is no whole-library sync, offline mode, database access, embedding, local
search index, model API, persistent user cache, or cross-workflow import.

API requests are sequential within a batch. Batching primarily reduces agent tool
round trips and repeated requests, not Zotero's necessary distinct requests.
Requests time out after 15 seconds; failures are surfaced without hidden retries.
Working data is sensitive library content: keep it local and clean it up after
verifying final edits. Cleanup deliberately refuses unmarked/moved folders.

APA formatting, exact-sentence matching, report backups and reference deduplication
remain agent tasks, described explicitly in the skill. This avoids expanding the
retrieval tool into a document workflow engine; the agent should not need to
generate executable code for them.

## Validation

```bash
conda activate cite-assist-dev
python -m pytest agent-python-cli/tests -q
```

Tests use mocked API responses for probe batching/provenance, metadata and text
reuse, pagination, partial failures, passage coverage, word boundaries and safe
cleanup. A live smoke test should also check `status`, `init`, a short search,
one shortlisted PDF retrieval, local passage re-reading and `cleanup`.