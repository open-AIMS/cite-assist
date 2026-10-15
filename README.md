# cite-assist

`cite-assist` explores workflows for finding references in an author's Zotero
library and adding APA citations to a report. The agent proposes sources and
assesses their support for a claim; the author reviews the evidence and approves
the final citations. Outputs require checking, including bibliographic details
and whether a source actually supports the statement.

This research project explores different approaches to implementing agents for
one citation workflow. It is not a rigorous benchmark, and a few example uses
cannot establish general conclusions about the best approach to building agents.
I nevertheless observed clear differences in workflow execution. In the
recorded Luna 6 example, the no-skill agent completed the task with several
missteps and 16 approval requests. The CLI-assisted agent used the supplied tools
without observed missteps and required 8 approvals. Both found the preferred
papers in their top three. These observations are a strong practical indication
of improvement in this example, not a statistical finding or a guarantee for
other tasks. The [evaluation index](eval_v2/README.md) records the metrics,
condition differences and limits, including higher token and credit use for
the CLI-assisted run than for the no-skill Luna run.

Most of the approaches trialled are only relevant to the current capabilities
of LLMs and the agent harnesses. We build most of the skills and code using
frontier large models for the SKILLs and code development, and tested using
much cheaper small models, such as Luna 6. The idea was that a workflow is
good if even a light weight model can successful complete the task. This also
means that the workflow is inexpensive to use.

We found that even todays frontier models (Oct 2026) can relatively efficiently
complete the simple form of the workflow with no SKILLs documents or support
code. The task is not that complicated, however, the goal was to use this as
a demonstration workflow that could be used for teaching purposes and it
solved a real problem.

## Current status

This repository contains two development phases. The current entry points are
the simplified workflows used for the eResearch conference presentation:

| Workflow | Starting point | Approach |
| --- | --- | --- |
| Natural-language skill | [agent-no-code/SKILL.md](agent-no-code/SKILL.md) | Instructions guide the agent's ordinary tools. No code is bundled, but the agent can write temporary helpers. |
| Skill with live Python CLI | [agent-python-cli/README.md](agent-python-cli/README.md) and [SKILL](agent-python-cli/SKILL.md) | Read-only tools search Zotero's local API and retrieve selected PDF passages. The agent supplies search strategy and judges evidence. No whole-library export or RAG index is required. |

The presentation focused on the value of an explicit skill compared with no
skill, then on bundling repeatable workflow steps into CLI tools. The manual
runs, original prompts, result notes and shortened recordings are indexed in
[eval_v2/README.md](eval_v2/README.md). They use one example sentence and are
illustrative, not a multi-model benchmark. The prompts differ by condition.

## Using the phase 2 skills

These skills were developed for the eResearch presentation research, not as a
packaged installation from GitHub. My preferred use is explicit
attachment in VS Code rather than installation under `.copilot/skills`:

1. Clone this repository to a local folder.
2. Open the report project where you want citation assistance in VS Code.
3. Use **File > Add Folder to Workspace** to add just the chosen skill folder
  from the clone: `agent-no-code` or `agent-python-cli`, not the whole repository.
4. Explicitly add that folder's `SKILL.md` to the chat prompt, along with the
  report passage and request. Identify the report project's root so the agent
  keeps report edits and temporary work there, not in the skill folder.

Keep Zotero desktop running with its local API enabled under **Settings >
Advanced > Allow other applications on this computer to communicate with
Zotero**. Both workflows use the personal library. The CLI workflow also requires
Python 3.10+; its [guide](agent-python-cli/README.md) describes the tools.
This method makes skill selection explicit and does not depend on automatic
discovery or activation. It is also how the skill-condition demonstrations ran.

Both phase 2 skills were tested at version `1.0.0`, recorded in their `SKILL.md`
metadata. When
changing instructions or bundled runtime code, increase the skill version and
record it in any new test notes. For the CLI skill, the version covers its
script as well as its instructions. See [the run records](eval_v2/README.md).

## Development history

**Phase 1: retrieval-augmented generation (RAG) and earlier experiments.**
Before the conference abstract was developed, the project implemented a local
Zotero text cache, BM25/embedding retrieval and LLM assessment of candidate
sources. It included an agent-driven CLI and a desktop app sharing that CLI,
plus an alternative live-API skill with starter code fragments. This work was
already published on GitHub during abstract preparation. It remains available
in [archive/README.md](archive/README.md); archived means historical, not invalid.

The phase also prototyped a sandbox and scoring harness intended for many
examples and models. It did not progress into the planned large evaluation.
Its protocol and limited results are distinct from the conference demonstrations.

I decided that the RAG approach, while valid, was probably over engineered and
complicated. The phase 2 result was instead to rely more heavily on the
abilities of the agents, and have the SKILL streamline their work. The phase 2
approach is simplier, but possibly less thorough in its search for papers.
Extensive benchmarking would be needed to verify this.

**Phase 2: simpler skills and live tools.** The conference work removed the need
to build and maintain a whole-library RAG system. The agent searched the live
library and judged retrieved evidence, either guided by instructions alone or
supported by a small Python CLI. The earlier RAG approach may offer more thorough
retrieval, but its complexity was not justified for this demonstration. These
runs do not establish comparative retrieval quality against the RAG system.

## Evaluation and presentation

The conference tests were run manually in an unrelated VS Code test folder.
For skill conditions, the complete skill folder was attached to the workspace
and its SKILL file was explicitly added to context, rather than relying on
automatic activation. The Zotero library was static and all requested tool
approvals were granted. [The evaluation index](eval_v2/README.md) records the
conditions, models, limitations and raw-to-edited recording correspondence.

Presentation sources, screenshots, draft builds and the submitted PowerPoint
remain in the local, Git-ignored `paper/` folder. The presentation drew on the
documentation, v2 screenshots and edited evaluation recordings. The draft was
then manually refined in PowerPoint, including text, layout and animations;
rebuilding a draft does not recreate the submitted deck. The v1 presentation
was an unused generation experiment and contributed no outputs to the final
presentation. Presentation files are not distributed in this Git repository.

## AI disclosure

AI assistance was used to develop the code, skill instructions, supporting
documentation and draft presentation materials. Phase 1 used Claude Opus 4.8
for AI-assisted coding. Phase 2 mainly used ChatGPT Sol 6.1 through GitHub
Copilot in VS Code. The agent workflows were developed with AI assistance.
The initial planned design was described, then an interative discussion was
used to ensure the approach and design was clear. These were then captured
in the various DESIGN markdown documents associated with each skill. The
phase 2 agents were simplified versions of those already developed in phase 1
and so rather than designing from scratch I started with the phase 1 SKILLs
documents and asked the agents to reimplement to follow the new approach.
Each SKILL was then tested and refined. The testing was performed by running
the workflow on an example. Where an agent went off track significantly the
workflow was stopped and the SKILL document corrected. Once the workflow could
complete I instructed the agent to review which aspects of the workflow could
be improved to streamline the process. This would start a design discussion
about potential improvements. Once they were agreed the agent was instructed
to update the SKILL and any associated code. This process was repeated until
the skill performed as expected.

Evaluation is a separate use of AI: the conference runs tested ChatGPT Luna 6,
with an additional ChatGPT Sol 6.1 no-skill run.

## Repository layout

```text
agent-no-code/         Current natural-language citation skill
agent-python-cli/      Current live Zotero CLI, skill and tests
eval_v2/               Manual conference test records and highlight clips
docs/                  Phase 2 development notes and working observations
archive/               Earlier implementations and prototype evaluation
  docs/                Phase 1 research, lessons and workflow diagrams
  agent-cli/           RAG CLI (refcite)
  agent-app/           RAG desktop app; imports sibling agent-cli
  agent-no-python-cli/ Earlier live-API skill with code fragments
  evaluation/          Prototype harness, datasets and limited results
_local/                Ignored raw recordings and editing working material
paper/                 Ignored presentation and abstract working material
```

[docs/README.md](docs/README.md) distinguishes current context from historical
research plans. Presentation background and style guidance are local inputs
beside the v2 draft builder in `paper/presentation/v2/draft/`.
Raw recordings travel with their editing scripts and notes
under `_local/recordings/`; shortened presentation clips remain in `eval_v2`.
Private Zotero caches, transient CLI responses and evaluation sandboxes are
excluded from version control. Ignoring a file does not remove earlier commits.

## Running tests

Create the development environment once from the repository root:

```bash
conda env create -f environment-dev.yml
```

Then run tests:

```bash
conda activate cite-assist-dev
python -m pytest agent-python-cli/tests -q
python -m pytest archive/agent-cli/tests archive/agent-app/tests -q
```

The live CLI requires Python 3.10+ and Zotero desktop's local API for actual use;
it has no third-party runtime dependencies. Its setup guide explains deployment
as a skill. The archived implementations have their own setup requirements.

## Licence

Original project code, skill instructions and documentation are available under
the [MIT licence](LICENSE), which permits reuse with the copyright and permission
notice retained. This does not grant rights to third-party papers, Zotero library
content, or third-party material shown in recordings. No Zotero library snapshot
has been archived; recording one is planned if a larger performance study is
undertaken.
