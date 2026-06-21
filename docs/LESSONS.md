# Lessons learned — agent mode vs app mode

A running register of observations from building and running cite-assist in its
two forms (**agent mode**: an LLM agent drives a generic harness; **app mode**: a
Python desktop app drives the loop and makes scoped LLM API calls). The purpose is
to inform the scientific write-up comparing the two approaches.

Each entry: an id, the date, the context, the lesson, and its implication for the
agent-vs-app comparison.

---

## L1 — App mode cannot match agent mode's investigative adaptivity

- **Date:** 2026-06-20
- **Context:** Deciding what to feed the model for the candidate-evaluation step.
  In agent mode the agent can decide mid-task that it needs more evidence and use
  the harness to read a candidate's full cached record, broaden a query, or re-run
  search. App mode gives the model only `review.md`.
- **Lesson:** An app-based workflow is inherently more rigid. Letting the model
  request and read more material on its own initiative would require giving it
  tools and a loop — i.e. rebuilding an agentic harness, which defeats the point of
  app mode. So app mode's evaluation is fixed-input by design.
- **Implication:** Expect app mode to underperform agent mode on claims where the
  decisive evidence is in the full text but not in the extracts surfaced into
  `review.md`. This is a structural limitation of the app approach, not a tuning
  problem, and is a key axis for the comparison.

## L2 — Fixed Python control flow lets app mode tolerate weaker models

- **Date:** 2026-06-20
- **Context:** Motivation for app mode. The hard part of agent mode for a small
  model is the orchestration: which command to run, in what order, with what
  arguments, and when to stop.
- **Lesson:** Moving orchestration into deterministic Python and asking the model
  only the scoped questions (which sentences to cite; classify them and write a
  query; which candidates to keep) should make app mode usable with weaker models
  than agent mode needs. The floor is not arbitrarily low, though: candidate
  evaluation requires enough scientific knowledge to judge claim–source alignment,
  so app mode targets *moderate* models, not very small ones.
- **Implication:** A useful comparison axis is the *minimum model capability* each
  mode needs to produce acceptable citations. App mode is expected to degrade more
  gracefully as model strength drops, down to the moderate floor set by the
  evaluation step.

## L3 — Stdlib-only token protection is obfuscation, not security

- **Date:** 2026-06-20
- **Context:** Storing the API token locally so the user sets it once, without
  adding a third-party dependency such as `keyring`.
- **Lesson:** With standard library only, the best achievable is reversible
  obfuscation: XOR the token with a random local keyfile so a disk-wide `grep` for
  token-shaped strings finds nothing. This stops untargeted malware but not a
  targeted attacker who reads both files or the source.
- **Implication:** Document the threat model honestly and offer an environment-
  variable path for users who need real secret protection. Do not overstate the
  guarantee.

## L4 — Stdlib tkinter has no cross-platform file drag-and-drop

- **Date:** 2026-06-20
- **Context:** Wanting drag-and-drop of a report file onto the app window without
  adding a dependency.
- **Lesson:** Plain `tkinter` supports no OS file drop on any platform; full
  cross-platform drop needs the third-party `tkinterdnd2`. Windows-only drop is
  reachable with the standard library alone via `ctypes` (Win32 `DragAcceptFiles`
  + `WM_DROPFILES`).
- **Decision/Implication:** App mode implements drag-and-drop on **Windows only**
  (the author's primary platform) and falls back to a File-open dialog everywhere.
  A convenience that is trivial in a full GUI framework costs platform-specific
  glue in a zero-dependency stdlib app — a small but real tax of the app approach
  worth noting in the comparison.

## L5 — Embodying a workflow in an app costs up-front problem decomposition

- **Date:** 2026-06-20
- **Context:** Deciding how app mode produces `claims.json`. In agent mode one
  model reasons fluidly across selecting sentences, classifying them, writing
  search queries, and (later) judging candidates. App mode cannot do this in one
  pass, because the author must be able to add or drop statements *before* the
  per-claim metadata (the `search_query`, which needs intelligence) is generated.
- **Lesson:** Having an application embody a workflow and control which calls are
  sent to the model requires significantly more engineering to break the problem
  down than a free-flowing agent harness does. Statement selection alone had to be
  split into two separate LLM calls (propose sentences; then generate claims) with
  a human edit step between them. The app gains deterministic control but loses the
  model's agentic adaptability — its freedom to reorder, revisit, or investigate
  mid-task.
- **Implication:** The decomposition effort is a core cost of the custom-harness
  approach and a central qualitative finding for the comparison. Each fixed call
  boundary the app imposes is a place the agent would otherwise have improvised.
