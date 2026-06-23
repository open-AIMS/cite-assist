# Cite-Assist — Research goals and evaluation

Status: Draft (study design; data collection not yet started)

This document records the research questions, evaluation method, and the
model/harness matrix for the comparison between **agent mode** (an LLM agent
drives a generic harness and shells out to the `refcite` CLI; see
[`DESIGN.md`](../agent-cli/DESIGN.md)) and **app mode** (a Python desktop GUI owns the loop
and makes scoped LLM API calls; see [`DESIGN-app-mode.md`](../agent-app/DESIGN-app-mode.md)).

It is deliberately separate from the two design documents: those define *what is
built*; this defines *what we are trying to learn and how we will measure it*.
Running observations accumulate in [`LESSONS.md`](./LESSONS.md).

---

## 1. Research questions

1. **Is it worth building a custom agent harness to implement a workflow, or are
   generic agent harnesses (VS Code + an agent) better?** What does the extra
   engineering of a tailored app buy, and what does it cost?

2. **Can a tailored harness make simpler models usable for a workflow?** If the
   hard control flow is fixed in Python and the model is asked only scoped
   questions, can a weaker model produce acceptable citations where the same model
   would struggle to drive a generic agent loop?

3. **What are the strengths and weaknesses of each approach?** In particular the
   trade-off between the app's deterministic control and the agent's fluid
   adaptability (the ability to investigate further, broaden a query, or read a
   full record mid-task).

This is a **single case study**, not a controlled trial. The aim is to get a feel
for the differences and to document them honestly, not to make strong general
claims about custom versus generic harnesses. Much of the comparison is
necessarily **qualitative** — the added development effort for the app, and its
rigidity, are judgements rather than measurements.

---

## 2. Materials

- **Reports.** Two reports written by the author, both drawing on the same Zotero
  collection. These provide realistic prose with known citation needs.
- **Zotero library.** The author's existing library (~300 records). It contains
  records for the project's own work **and** records for unrelated work, so a
  successful run must select the right sources, not merely any plausible match.
- **Gold citations.** A set of known-good citations that should be inserted for
  the test reports, used as the reference for precision/recall-style scoring.

---

## 3. What we measure

**Quantitative (run with full automation, no human in the loop, to isolate model
and harness effects):**

- **Workflow success rate.** Did the end-to-end run complete and produce inserted
  citations without manual intervention or a broken state?
- **Citation quality against the gold set.** Which expected citations were
  inserted, which were missed, and which spurious citations were added.
- **Token cost.** Tokens consumed per run (and, where available, wall-clock time),
  to compare the efficiency of the two control strategies and of different models.

To support this, **app mode logs per-session metrics** (model, per-call token
counts, latencies, claims proposed/kept, and run outcome). The walking skeleton
(Stage A1 in `DESIGN-app-mode.md`) puts this logging in place before any LLM call,
so no comparison data is lost. Agent-mode runs capture the equivalent token and
outcome data from the harness.

**Qualitative:**

- **Difficulty of making corrections** in each implementation — how easily a human
  can intervene, steer, or fix a wrong result in the app versus the agent.
- **Development effort and rigidity** of the app harness relative to the generic
  harness (drawn from `LESSONS.md`).
- **How often agent mode actually uses its adaptive abilities** — specifically how
  often it chooses to read a full cached record, broaden a query, or re-run search
  mid-task. If this is rare (as expected), it argues against building that
  capability into app mode; if common, it is a real advantage of the agent
  approach. This frequency is itself a finding.

---

## 4. Method (automated comparison)

For each (report, mode, model) combination:

1. Start from the same synced Zotero cache and the same report prose.
2. Run the full workflow with the human removed from the loop (statements accepted
   as proposed, candidate recommendations accepted as returned).
3. Record the inserted citations, the token cost, and whether the run succeeded.
4. Score the inserted citations against the gold set.

Full automation here is a measurement device, not the intended product use: it
removes the human variable so differences are attributable to the model and
harness. The *human-in-the-loop* experience (correcting, steering) is assessed
qualitatively in normal interactive use.

### Standardisation caveats

We standardise where practical (same reports, same library, same gold set, same
models where a model is shared across modes) but do not over-invest in forcing the
two modes to be identical. Aligning every prompt and removing every affordance
difference would, by construction, drive both modes toward the same behaviour and
erase exactly the differences we are studying. The point of the comparison is the
**quality of the implementation achievable for a given level of effort**, so the
agent harness's ability to do more with a few sentences of instruction is part of
what is being assessed, not a confound to be eliminated.

---

## 5. Model / harness matrix

**Agent mode (generic harnesses):**

- **VS Code + GitHub Copilot** with Opus 4.8 and GPT-5.4 mini.
- **VS Code + Continue** with a local model setup running **Gemma 4 27B**, and
  larger models via **OpenRouter** (Opus 4.8, GPT-5.4 mini).

**App mode (scoped LLM API calls):**

- **Opus 4.8**, **GPT-5.4**, and **Gemma 4**.

This lets shared models (the Opus and GPT families, and Gemma) be compared across
both modes, addressing research question 2 — whether the tailored harness lets a
given model, especially a weaker one, succeed where it would struggle to drive a
generic agent loop. App mode targets **moderate** models, not very small ones: the
candidate-evaluation step (LLM call 3 in `DESIGN-app-mode.md`) requires enough
scientific knowledge to judge the alignment between a claim and a candidate
source.

---

## 6. Development effort and cost (case-study record)

A central part of research question 1 is the **engineering cost** of each
approach: is it worth building a tailored app, or is a SKILL for a generic agent
harness the better return on effort? This section records the actual development
time and AI token spend for this build, to feed the write-up directly. The
figures are a single developer's measured effort for this one case study, not a
benchmark; they are AI-assisted development costs, and the **token spend here is
development-time cost, not the per-run inference cost** measured in §3.

All development was done with **Claude Opus 4.8 (High)** in VS Code. Agent-mode
workflow testing additionally used a mix of **Claude Opus** and **Gemini 3.5
Flash**.

| Approach | Development | Dev token cost | Workflow-testing token cost | Total tokens |
|----------|-------------|----------------|-----------------------------|--------------|
| **Agent CLI** (SKILL + `refcite` Python backend + testing) | 15.3 h | $51.40 | $9.84 | $61.24 |
| **Agent App** (`citeapp` layer over the shared backend) | 5.75 h | $39.60 | — | $39.60 |
| **Agent No CLI** (SKILL file only) | 2 h | $2.76 | - | $2.76 |
| **Test harness** | 4 hrs | $13.00 | - | $13.00

We needed to refactor the repository layout to have three separate folders for the multiple implementations. This took 49 min and $4.

**How to read these figures.** The two numbers are **not** independent builds of
the same thing. The agent-mode figure includes developing the `refcite` backend
(cache, search, ranking, insertion, the CLI) and the SKILL that drives it. App
mode **reuses that backend unchanged** through its declared integration surface
(A31 in `DESIGN-app-mode.md`); its 5.75 h and $39.60 are the **incremental** cost
of the `citeapp` GUI layer and the three scoped LLM calls built on top. So the
honest reading is:

- The **shared foundation** (the `refcite` backend) is counted in the agent-mode
  row, because it was built first to serve agent mode.
- The **extra development to add the app** on top of that foundation was **5.75 h
  and $39.60** — a direct answer to "how much extra development is needed to build
  the app?".
- A generic-harness SKILL is the cheaper way to *first* make the workflow usable;
  the app is a moderate additional investment that buys deterministic control and
  the ability to use weaker models (research question 2), at the cost of the
  rigidity documented in `LESSONS.md` (Lesson L1).

These totals are indicative of AI-assisted development by one experienced
developer who already understood the problem domain; a different developer, model,
or prompting style would shift them. Per-stage breakdowns (Stages A0–A4 in
`DESIGN-app-mode.md`) were not timed separately and are not reported here.

---

## 7. Log of work

This section just contains notes during the testing and use phase of the agents.
2026-06-22: 1 hr 39 min
We spent most of the time refining the test harness workflows. Getting the rebuilding
of the sandbox correct, performing two runs of the cli agent, then analysing the 
performance of the agent. This indicated that we probably need to improve the citations
in the test text, indicating more acceptable citations and checking that the citations
are as good as possible. We also need to potentially adjust the scoring. The agent-cli
with claude opus 4.8 got 2 good, 2 wrong, 2 miss, 1 false-positive → −1.0, with the 
existing scoring. This also indicates that we need text sections that we do for development
and ones that we use for evaluation.


---

## 7. Known limitations

- **n-of-1.** One author, two reports, one library. Findings are indicative, not
  generalisable; this is framed as a case study throughout.
- **Asymmetric affordances are intentional.** Agent mode permits mid-loop steering
  and investigation that app mode forbids by design (Lesson L1). Rather than
  suppressing this difference, we measure how often it matters (§3, qualitative)
  and treat the affordance gap as a finding.
- **Qualitative core.** The headline comparison — development cost versus
  adaptability — rests on engineering judgement recorded in `LESSONS.md`, not on a
  metric.
