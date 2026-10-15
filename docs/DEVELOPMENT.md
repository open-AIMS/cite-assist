# Phase 2 development notes

These records were separated from the phase 1 research document during repository
cleanup. They describe development, not the recorded conference evaluation.
See [the evaluation index](../eval_v2/README.md) for test conditions and results.
The [root README](../README.md) records AI use and the two development phases.

## Recorded development effort

Both phase 2 workflows build on phase 1 rather than starting from scratch.
Sol 6.1 was tasked to derive `agent-no-code` from
[the earlier live-API skill](../archive/agent-no-python-cli/SKILL.md), removing code
from the skill. `agent-python-cli` then extended that new no-code workflow to
capture repeatable Zotero interactions in bundled tools.

The wall time for this development was recorded, including the prompting, discussions,
generation and testing until the final skill was ready for recording the final run. 
The dollar figures cover the development and testing sessions. They are not a valuation 
of human labour and do not include the inherited phase 1 effort. The figures therefore 
describe incremental phase 2 development, not the cost of independently recreating either workflow.

| Implementation | Recorded development time | Recorded development cost | Model label in original row |
| --- | --- | --- | --- |
| Agent No code | 0.5 hr | $0.68 | Sol 6.1 |
| Agent Python CLI | 1.8 hr | $2.75 | Sol 6.1 |

## Original log entries

2026-10-14: 0.5 hr
After running the no-python-cli using Sol I noticed that it took 11 coding requests that
require 'Allow' through VSCode. I asked to potentially streamline the workflow with some
batching. Its initial approach was to add significant Python to manage a local cache of
of information obtained from Zotero. This involved adding a lot of Python inside the
SKILL file. I felt this was not inline with the test and so we kept the about of code in
the SKILL to just the small helpers to access Zotero, but added additional instructions
about batching.

2026-10-14: 0.5 hr
Converted the no-python-cli to a simplier agent-no-code version using Sol-6.1. The goal is
that this is the baseline set of additional instruction above no instructions. The timing
of this workflow is slightly off because I developed it starting from no-python-cli.
28.8 credits to make the first draft. 39 credits for a full test run and update.