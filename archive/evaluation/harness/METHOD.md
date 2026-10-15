# Test harness — method and protocol

This document is the operational protocol for the cite-assist preliminary
evaluation. It is the paper-facing companion to the study design in the
cite-assist repo's `docs/RESEARCH.md` (research questions, materials, model
matrix). RESEARCH.md says *what we are trying to learn*; this says *exactly how a
run is set up, executed, scored, and aggregated* in this tests repo.

It is a small-scale, single-author **case study** (n-of-1 library, three intro
texts), framed as preliminary throughout.

---

## 1. What is compared

Three implementations of the same workflow (add APA citations to a report from a
local Zotero library), each driven by a capable and a less-capable model:

| Lane | Implementation | Model | Ranking | Provider |
|------|----------------|-------|---------|----------|
| `cli_opus48`    | agent-cli            | Opus 4.8        | hybrid (embeddinggemma-300m-qat) | Copilot |
| `cli_haiku45`   | agent-cli            | Haiku 4.5       | hybrid (embeddinggemma-300m-qat) | Copilot |
| `nocli_opus48`  | agent-no-python-cli  | Opus 4.8        | LLM-only | Copilot |
| `nocli_haiku45` | agent-no-python-cli  | Haiku 4.5       | LLM-only | Copilot |
| `app_gemmaE4B`  | agent-app            | gemma-4-e4b     | hybrid (embeddinggemma-300m-qat) | LM Studio |
| `app_gemma31b`  | agent-app            | gemma-4-31b-qat | hybrid (embeddinggemma-300m-qat) | LM Studio |
| `app_opus48or`  | agent-app            | Opus 4.8        | hybrid (embeddinggemma-300m-qat) | OpenRouter |

The full planned matrix (lanes × texts) is `run-index.csv`. `run_id` is
`<lane>_<text>`, e.g. `cli_opus48_t1`.

## 2. Datasets and the gold standard

Each test is a **self-contained passage** (a report introduction of four or five
paragraphs) given to every implementation in full, so context-reading agents are
not handicapped. Source texts live in `datasets/` (`t1`, `t2`, `t3`; the long-run
aim is ~10).

The gold standard is encoded **inline** in the dataset text:

- A trailing APA parenthetical on a sentence is that sentence's citation **pool**;
  entries are separated by `;`.
- A plain entry is **gold** (the ideal citation the author actually used); a `*`
  suffix marks an **acceptable** (ok) alternative.
- A sentence with no trailing citation expects **no** citation; a citation
  inserted there is a false positive.
- Narrative citations are normalised to parenthetical form when tagging
  (`Beaman (2010) compiled…` → `…compiled (Beaman, 2010)`) so the stripped input
  carries no author hint and gold is directly comparable to inserted output.

`harness/cite_eval.py` parses this format, derives the structured gold, and
auto-produces the hint-free **input** text by removing every citation
parenthetical. Each text is kept to ~8 citable statements (the workflow degrades
beyond ~8 references / ~80 candidates per review).

## 3. Anti-leakage (sandboxing)

A run never executes in this repo. `harness/make_run.py` seeds a per-
implementation sandbox under `sandboxes/<impl>/` containing only: the stripped
input report, an empty `references.md`, the implementation itself (agent modes),
and `RUN.md`. That folder is opened as its own VS Code workspace (agent modes) or
pointed at by the GUI (app mode). The gold texts (`datasets/`), the scorer, and
other runs' results are not in that workspace.

Limitation (recorded honestly): a terminal-capable agent could in principle read
an absolute path outside its workspace. Isolation relies on the gold not being in
the opened workspace and not being named in any prompt; this is adequate for
cooperative preliminary testing, not an adversarial guarantee.

## 4. Running a single test

```bash
# 1. set up the sandbox (writes stripped report, references.md, RUN.md, manifest)
python harness/make_run.py --impl agent-no-python-cli --text t1 --model opus48

# 2. open sandboxes/agent-no-python-cli as the workspace, select the model named
#    in RUN.md, and run the workflow following RUN.md's accept-all policy.

# 3. collect + score
python harness/score.py sandboxes/agent-no-python-cli
```

**Accept-all policy.** Metric 1 keeps the existing workflows unmodified: the
operator accepts the statements the model proposes and the top candidate per
claim, and does not steer the model's control flow. A run *completes* if it
reaches inserted citations without the operator having to push it back on track
(merely clicking "accept" is not steering). How a run broke is recorded in the
`notes` column for the qualitative commentary.

App mode has no agent loop to drive; the operator runs the GUI (launched from the
cite-assist repo) against the sandbox's report and mirrors the accept-all policy.

## 5. Scoring

`harness/score.py` aligns the edited report's sentences to the gold sentences by
normalised prose (case-, whitespace-, quote- and dash-insensitive). A gold
sentence whose prose cannot be matched is an **alignment failure** — rewording is
treated as a workflow failure by design, and flags the run `alignment_ok=False`.

Per gold sentence:

| Gold sentence | Inserted | Outcome | Points |
|---|---|---|---|
| citable | a gold entry | good | +1.0 |
| citable | only an ok entry, pool has a gold | partial | +0.5 |
| citable | only an ok entry, pool is ok-only | ok (best available) | +1.0 |
| citable | a citation not in the pool | wrong | −1.0 |
| citable | nothing | miss | 0.0 |
| citable | pool match **plus** an extra non-pool citation | spurious | −0.5 each |
| bare | any citation | false positive | −1.0 each |
| bare | nothing | ok_bare | 0.0 |

The ok-only rule implements the requirement that settling for the only acceptable
source when no great match exists is correct, not penalised. Citation identity is
matched on normalised first-author signature + year (`roberts|2002`), robust to
`et al.`/`&`/`and` variants. Duplicate same-author-year works are out of scope.

Outputs: `results/<run_id>/` holds `report.md`, `references.md`, `meta.json`, and
`score.json`; one row per run is upserted into `results/results.csv`.

## 6. Recording results

`results/results.csv` is the master, manually editable. The scorer fills the
computed columns; the operator fills the observational/Copilot columns by hand:

- computed: `alignment_ok, citations_inserted, n_citable, n_good, n_ok,
  n_partial, n_wrong, n_miss, n_false_pos, n_spurious, n_align_fail, score`
- manual: `completed_no_steering` (the Metric 1 judgement), `copilot_credits`
  (from the VS Code Copilot usage panel), `tokens`, `seconds`, `notes`.

## 7. Aggregation

`harness/analyze.py` reports:

- **Metric 1** — completed runs per lane (uses `completed_no_steering`, falling
  back to the `citations_inserted`/`alignment_ok` proxies when blank).
- **Metric 2** — mean score and the good/ok/partial/wrong/miss/false-positive
  breakdown, **only for lanes with at least `min_samples_for_quality` completed
  runs** (`config.json`, default 3). Lanes below the threshold are listed as
  insufficient samples, so a single lucky perfect run cannot inflate a lane that
  otherwise fails. Metric 1 thus gates which lanes are eligible for Metric 2.

## 8. Known limitations

- n-of-1 library, three texts; indicative only.
- Stochastic single runs per cell given the time budget; report n and note
  variance where a cell is repeated.
- Correctness is defined by the author's pools; a reasonable source the author
  did not list scores as wrong (mitigated by `*` acceptable entries).
- Affordance asymmetry between modes is intentional and treated as a finding, not
  a confound (see RESEARCH.md §7).
