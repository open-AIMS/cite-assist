# cite-assist

`cite-assist` helps an author add **APA** citations to a report, drawing on
their own Zotero library. It finds sources that support statements in the
report, writes a human-reviewable hand-off, and inserts APA in-text citations
plus a shared reference list. It never fabricates citations.

This repository holds **three implementations of the same workflow**. They exist
side by side so the approaches can be compared for a research study (see
[docs/RESEARCH.md](docs/RESEARCH.md)). Each lives in its own top-level folder.

## The shared workflow

Every implementation follows the same high-level loop, alternating between the
author and the agent. Only *how* each step is carried out differs between them.

![The high-level cite-assist workflow shared by all three implementations: the author selects text, the agent proposes citable statements, the author confirms or edits them, the agent finds candidate citations from the Zotero library and shortlists them, the author reviews the candidates, and the agent inserts the citations and references.](docs/workflow-common.svg)

## The three implementations

| Folder | Implementation | What drives the workflow |
|--------|----------------|--------------------------|
| [agent-cli/](agent-cli/) | **Agent mode** | A generic agent harness (VS Code Copilot, Codex, Claude Code) reads a `SKILL.md` and shells out to the bespoke `refcite` Python CLI (offline Zotero cache + BM25 / embeddings RAG search). |
| [agent-app/](agent-app/) | **App mode** | A lightweight `tkinter` desktop GUI owns the loop and makes scoped LLM API calls. It reuses the same `refcite` CLI as a library. |
| [agent-no-python-cli/](agent-no-python-cli/) | **Agent, no Python CLI** | A single `SKILL.md` with no Python tool at all. The agent does the whole job from the skill instructions. The "ultimate test" of how much a skill file alone can carry. |

App mode is included as an agent workflow even though a fixed Python program,
not an LLM agent, owns the orchestration loop. The point of comparison is the
workflow, not the harness.

## Shared code

Agent mode and app mode **share the same `refcite` Python CLI**. To keep agent
mode installable as a single self-contained skill folder, `refcite` lives at
[agent-cli/refcite/](agent-cli/refcite/). App mode imports it from there: a small
`sys.path` bootstrap in [agent-app/citeapp/\_\_init\_\_.py](agent-app/citeapp/__init__.py)
adds the sibling `agent-cli/` folder so `import refcite` resolves wherever the
app is launched from. The dependency runs one way only; `refcite` never imports
`citeapp`.

`agent-no-python-cli` shares no code with the other two; it is skill text only.

## Where to start

- **Install and use agent mode:** [agent-cli/README.md](agent-cli/README.md) and
  [agent-cli/INSTALL.md](agent-cli/INSTALL.md).
- **Agent-mode design:** [agent-cli/DESIGN.md](agent-cli/DESIGN.md).
- **App-mode design:** [agent-app/DESIGN-app-mode.md](agent-app/DESIGN-app-mode.md).
- **No-Python-CLI design:** [agent-no-python-cli/DESIGN-agent-no-python-cli.md](agent-no-python-cli/DESIGN-agent-no-python-cli.md).
- **Research goals and evaluation:** [docs/RESEARCH.md](docs/RESEARCH.md) and
  [docs/LESSONS.md](docs/LESSONS.md).

## Repository layout

```
agent-cli/             Agent mode: SKILL.md + the shared refcite CLI
  refcite/             the shared Python CLI (imported by app mode too)
  cite-assist.toml     refcite configuration
  SKILL.md, README.md, INSTALL.md, DESIGN.md
  tests/               refcite tests
agent-app/             App mode: the tkinter desktop GUI
  app.py, citeapp/     the GUI app (imports refcite from agent-cli)
  spikes/              throwaway risk-reduction spikes
  DESIGN-app-mode.md
  tests/               app-mode tests
agent-no-python-cli/   Skill-only implementation (no Python)
docs/                  Cross-cutting research notes (RESEARCH.md, LESSONS.md)
paper/                 Conference abstract / submission material
```

## Running the tests

Each implementation has its own test suite. From the repository root:

```bash
python -m pytest agent-cli/tests   # refcite (shared CLI)
python -m pytest agent-app/tests   # citeapp (app mode)
```

Each suite's `conftest.py` adds the folders it needs to `sys.path`, so no
install or `PYTHONPATH` is required to run them.
